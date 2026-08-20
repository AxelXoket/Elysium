<p align="center">
  <h1 align="center">Elysium</h1>
  <p align="center">
    <strong>Privacy-first, localhost-only AI character chat client powered by OpenRouter</strong>
  </p>
  <p align="center">
    <img src="https://img.shields.io/badge/python-3.13-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python">
    <img src="https://img.shields.io/badge/license-MIT-blue?style=flat-square" alt="License">
    <img src="https://img.shields.io/badge/version-1.1.5-brightgreen?style=flat-square" alt="Version">
    <img src="https://img.shields.io/badge/privacy-ZDR_enforced-brightgreen?style=flat-square" alt="Privacy">
    <img src="https://img.shields.io/badge/at--rest-SQLCipher_vault-brightgreen?style=flat-square" alt="Encryption">
    <img src="https://img.shields.io/badge/streaming-SSE-brightgreen?style=flat-square" alt="Streaming">
    <img src="https://img.shields.io/badge/frontend_tests-1655_passed-success?style=flat-square" alt="Frontend Tests">
    <img src="https://img.shields.io/badge/frontend-React_19-61DAFB?style=flat-square&logo=react&logoColor=white" alt="React">
  </p>
  <p align="center">
    <img src="assets/elysium_banner.png" alt="Elysium" width="820">
  </p>
</p>

---

Elysium is a privacy-first AI character chat client that routes **all model traffic through a local FastAPI backend**. The frontend never contacts OpenRouter directly. Your entire chat database - messages, characters, personas, attached images, your notebook and limits, and your API key - is **passphrase-encrypted at rest (SQLCipher)**, strict ZDR privacy routing is enforced on every request, and raw upstream error bodies are never exposed to the client. It runs as a dev server pair or as a **packaged Windows desktop app** (`Elysium.exe`).

## What's new in v1.1.5

- **A notebook, one per chat** - a place for what the story has established, sent with every message so the model stops forgetting. You write notes yourself, or let a small model read the last turns and suggest them. A note that supersedes another retires it rather than deleting it
- **Limits that outrank the story** - a separate list of things you never want written, kept apart from the notebook because it has a different life: limits never expire, never merge, and are never trimmed to make room. If they cannot fit the model's context, the app refuses to send rather than quietly dropping them
- **A hard daily ceiling on what the notebook may spend** - sixty calls a day, counted in the database so a restart does not reset it, and enforced before the request rather than warned about after it
- **The conversation is closed to the accessibility tree, and this switch defaults ON** - an unprivileged program running as the same user could read the chat title, the character name and the message bodies out of WebView2's accessibility tree, verbatim. Hiding the window from screen capture is no defence against it: capture exclusion hides pixels, and this is text. The cost is real and is stated rather than buried, since while it is on a screen reader cannot read Elysium either
- **Hide the window from screen capture** - Settings > Security. Screenshots, screen recording and screen sharing see a blank window. Off by default, stored in the vault, and deliberately not applied while the vault is locked
- **"Start over instead"** - there is no passphrase recovery, so the only honest answer to a lost passphrase is starting over. A quiet link on the lock screen explains exactly what goes before it asks for the phrase
- **Check the key you already stored** - Settings > Security can ask OpenRouter whether the stored key is still accepted, without you retyping it. "Rejected" and "could not reach OpenRouter" stay two different answers, because they are opposite instructions
- **A cut-off reply now says so** - a reply that ended because the provider hit its token ceiling, or because the connection closed without saying how it ended, reads as trimmed short instead of as one that simply stopped talking

Security work in the same release. **[SECURITY.md](SECURITY.md)** describes
each of these in full, including what it does not close:

- **Your own machine is no longer a permitted destination for your API key** - `127.0.0.1`, `localhost` and `::1` were on the egress allowlist unconditionally, for two reasons that were measured and turned out to be wrong. They now take the same deliberate opt-in as any other address
- **The WebView2 environment variables are replaced rather than added to** - all nine, whether the accessibility switch is on or off. They can hand the browser engine drawing your conversation a debugging port, a redirected profile folder, or a different browser binary, and any program running as you can set them with one `setx`
- **Deleting one sixteen-byte file no longer destroys the vault** - `salt.bin` had no second copy anywhere: delete it, or flip a single byte in it, and the correct passphrase opened nothing ever again. `vault.recovery` beside it now carries the salt and its cost settings, and the app repairs itself from it. Your data folder is otherwise untouched: no permissions are changed, and it copies, moves and deletes like any other folder
- **The vault reset route does not exist outside the installed app** - previously a development checkout could be wiped by one local request carrying the confirmation phrase, with no passphrase and nothing else in the way
- **The recorded voice interpreter is checked before it is run** - `runtimes.json` names a program the app launches, and any process running as you can write that file. The path is now confined to the folder Elysium installs into and the binary is fingerprinted at install time. What that does not close is written down rather than glossed: an attacker who can write inside that folder has other ways in

## Since v1.1.5

Everything added and fixed since the last release is in
**[CHANGELOG.md](CHANGELOG.md)**. It becomes the v1.2.0 notes when the version
moves - v1.2.0 is the release RAG lands in; keeping it out of here is what stops this file growing a new section per
release and never losing one.

## Features

- **Passphrase Vault** - The whole database (characters, chats, messages, personas, settings, attached image bytes, and your OpenRouter API key + proxy URL) is a SQLCipher-encrypted file. The app starts locked; a passphrase unlocks it (scrypt-derived raw key, held only in RAM). Change the passphrase from the Security tab; closing the desktop app locks the vault. There is still no way to recover a forgotten passphrase, but the lock screen's "Forgot your passphrase?" link is a way out of one - it wipes the vault and starts over rather than leaving you locked out for good; see Known Limitations for exactly what that destroys and what it cannot reach. Three things sit outside the vault: spoken replies, written as plain `.wav` under the data folder and wiped at every lock, launch and shutdown; the reference clip you record for a voice model that CLONES, with a transcript of it, which nothing purges; and the decorative chat wallpaper, which lives in local browser storage. [SECURITY.md](SECURITY.md) has the full list
- **Character System** - Create, import (Character Card V2 JSON), and manage characters with full field support (system prompt, description, personality, scenario, example dialogue, post-history instruction)
- **Persona System** - Create and switch AI personas that are injected as a system block into every completion request
- **Streaming Responses** - Token-by-token SSE streaming with a live cursor and a Stop control. However a reply ends early - you press Stop, the provider drops out mid-sentence, the model hits its own token limit - the text you have already read is kept and marked: a small "Truncated" label with a scissors icon at the message's bottom-left, so a cut-short reply never reads as one that simply finished. Only a send that produced nothing rolls back and restores your draft
- **Response Variants** - Regenerate keeps every take: swipe between variants in a carousel and pick which one the conversation continues from
- **OpenRouter Integration** - Browse and select from the full OpenRouter model catalogue; generation parameters (temperature, top\_p, top\_k, max\_tokens, seed, repetition\_penalty) are validated, model-filtered, and forwarded
- **Context Budget** - App-level `context_budget_tokens` controls history trimming; oldest messages are dropped to fit the budget - never forwarded to OpenRouter as a provider field
- **Message Lifecycle** - Send (streaming + optimistic UI), regenerate with variant history, delete (target + all following), clear chat, and rename chats inline from the sidebar
- **Copy Out** - Every message carries a copy button that puts the original text on the clipboard, asterisks and all, not the styled version on screen; text selection is on window-wide, so dragging across a reply and pressing Ctrl+C works too, with timestamps and variant counters kept out of the selection. What happens to a copy once it leaves is Windows' business - [SECURITY.md](SECURITY.md) covers Clipboard History and its sync
- **Stop Sequences** - Up to 4 stop sequences (with `\n` support) managed as chips in Generation Settings; always forwarded to the provider, mirroring the backend rule
- **Image Attachments** - Attach up to 4 images (PNG/JPEG/WebP) per message to vision-capable models; drag-in/paste/pick, thumbnail strip, full-size lightbox. The attach UI is gated by the model's image modality, images are downscaled and content-addressed server-side, and the backend builds the provider payload (the frontend never constructs image URLs)
- **Reading & Ambience Settings** - In-app settings for message font size and line height, `*narration*` styling, an optional chat wallpaper with framing, zoom, contrast/tint controls and adaptive text, message-bubble solidity, and a living WebGL mist backdrop (with a static fallback)
- **Notebook** - a per-chat list of what the story has established, sent with every message. You write the notes, or let a small model of your choosing read the last turns and suggest them - off until you pick one, because it is your API key. Notes are never deleted behind your back: a superseded one retires, and one that does not fit the ceiling stays in the panel saying why. What the model writes lives in its own weaker block, after the history, and says who wrote it
- **Limits** - a separate list of what you never want written, `everywhere` or `this chat`. Never expire, never merge, and **never trimmed to make room**: if they do not fit the model's context, the app refuses to send rather than quietly dropping them. Any chat can be told to ignore the global set
- **A daily ceiling on the notebook's spending** - sixty calls, counted in the database so a restart does not reset it, blocked before the request rather than warned about after. Repeated failures pause the reader; enough of them stop it until you say otherwise. The Notes tab shows runs, calls used today, credits spent, and why anything was skipped, with the lifetime call count and lifetime credits shown beside today's
- **Screen privacy** - Settings › Security can hide the window from screenshots, screen recording and screen sharing. Off by default, stored in the vault, and not applied while the vault is locked. A layer, not a guarantee
- **The conversation is closed to the accessibility tree** - the embedded browser publishes the whole page to Windows' accessibility layer as text, and an audit read the chat title, the character name and the message bodies straight out of it with an unprivileged program running as the same user: no passphrase, no launch token, nothing to click. Screen-capture exclusion does not touch that path, because it hides pixels and this is text. Elysium now starts its browser with that tree switched off, and this is the one protection here that is ON when nobody asked for it. The cost is not hidden: while it is on, a screen reader cannot read this app at all. `setx ELYSIUM_ACCESSIBILITY_PRIVACY 0` and a restart is the whole way out - an environment variable rather than a checkbox, on purpose, because somebody who needs a screen reader cannot navigate to a setting inside an app they cannot read, and the switch has to be reachable before the vault is unlocked. Only exactly `0` turns it off, and the launch log names the state that took effect
- **Sidebar Navigation** - Persona strip with a switcher, client-side character search, and New Chat / New Character docks
- **Active Context Preview + live context meter** - Local-only collapsible card in the Models tab showing what the next request will include (model, persona, character, message count, generation params, context budget, notebook size) plus a live "≈ used / capacity tokens" gauge on the selected model; approximate estimates, never the exact provider payload
- **Error Toast System** - Centralized safe error notifications over the chat canvas; auto-dismiss after 4.5 s, max 5 visible, extras queued
- **Privacy by Design** - the provider policy is hardcoded backend-side and cannot be overridden from anywhere; see [Privacy Contract](#privacy-contract) for the exact fields and the full list of what is and is not sent
- **Sealed Secrets** - API key and proxy URL live inside the encrypted vault (unreadable while locked); a one-time migration moves them out of the OS keyring and deletes the old entries - never sent to the frontend. Settings > Security can test the stored key against OpenRouter without you retyping it - "rejected" and "could not reach OpenRouter" are reported as two different answers, since only one of them means the key is bad
- **Strict CORS + Host allowlist** - Backend accepts browser requests from `http://127.0.0.1:5173` only and rejects foreign `Host` headers (DNS-rebinding shield)
- **Locks itself when idle** - after 5 minutes of doing nothing the vault closes: the key leaves memory, the voice model is unloaded and the GPU memory comes back. Change the delay or turn it off in Settings > Security. A reply that is still streaming counts as activity for as long as it runs - a background note extraction deliberately does NOT, and the lock cancels its planning loop. It does not cancel a reply that has already arrived: the lock waits up to five seconds for that one to be written
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
│   Notes      │                                           │
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
│  ┌────────────────────────┐   ┌──────────────────────┐ │
│  │ notebook (notes+limits)│   │ note reader (worker) │ │
│  └────────────────────────┘   └──────────┬───────────┘ │
│                                                          │
│  SQLCipher vault (DB + images + secrets, WAL) · httpx     │
│  (trust_env=False) · 423 vault gate while locked         │
│                                                          │
│  PROVIDER_POLICY (hardcoded, immutable):                 │
│    zdr=true · data_collection=deny · allow_fallbacks=false│
└──────────────────────────────┬──────────────────────────┘
                               │ HTTPS · Authorization only
                               │ TWO senders, one host: the chat you
                               │ sent, and the note reader running
                               │ unattended on the same key
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
- `raw_json`, `avatar_path`, `tools`, `tool_choice` - **never** sent. `response_format` is sent by exactly two paths, the notebook's background reader and its "Try it on this chat" preview, and only ever as the same fixed schema defined in this repository: no request that carries your conversation to a chat model carries it, and nothing the frontend sends can add it. Until v1.2 nothing sent it at all, and this line said so. `image_url` parts are built server-side **only** for images the user explicitly attached (vision models); the frontend never constructs them
- Raw upstream OpenRouter error bodies are never forwarded to the client - safe mapped codes only, on the streaming path as well as the plain one. Streaming otherwise uses the same hardcoded provider policy, relaying deltas untouched
- API key is sealed inside the encrypted vault (unreachable while locked); never returned by any endpoint, never logged
- Browser storage holds only UI preferences - never messages, personas, characters, API keys, or proxy URLs
- Frontend never emits an `Authorization` header - all provider auth happens backend-side
- Logs carry ids, counts, and status codes only - never message content, passphrases, or key material
- Every response carries a Content-Security-Policy plus `X-Content-Type-Options: nosniff` and `X-Frame-Options: DENY`, including the 423/403/400 refusals that never reach a route. The policy exists for containment rather than for XSS: the same local origin serves the whole API, so `default-src 'self'` means nothing running there has anywhere to send data. The packaged build additionally sends `Cross-Origin-Resource-Policy: same-origin`, which stops a remote page that guesses the port from probing your attachments through an `<img>` tag
- A stored image is served only if its recorded type is one the app itself produced (PNG/JPEG/WebP); anything else is refused rather than handed to the browser
- The key is derived with scrypt at OWASP's current floor (N=2^17, r=8, p=1). The parameters are recorded per vault, so an older vault keeps opening under the ones it was made with and is re-derived to the current ones the next time it is unlocked - the one moment the passphrase exists in memory and a re-key is possible at all
- A passphrase must be at least 12 characters and must not be a repeated fragment, a keyboard walk, or one of the phrases any guessing attempt starts with. There is no rate limit behind this vault - somebody with the folder guesses offline - so length and variety are what matter, and composition rules (one capital, one digit, one symbol) are deliberately NOT imposed
- Locking overwrites the key in memory rather than dropping the reference. Optionally the vault locks itself after a chosen idle period; a chat request still in flight counts as activity, so a streamed reply is never cut short, but a background note extraction does not count as activity, and the lock cancels its planning loop. It does NOT cancel a reply that has already arrived: the lock waits up to five seconds for that one to be written, so a note can land in the seconds after you press Lock
- The desktop window is given a secret at launch and every API request carries it. Loopback is not a permission boundary: without this, any program running as your user could read the whole conversation over HTTP while Elysium is open, which is exactly when the vault is unlocked. The secret travels in the URL fragment (never sent to a server, never logged), is read once and stripped from the address, and is kept in memory rather than in browser storage. It is also withheld from every subprocess Elysium starts, since the voice engine and the installer both run code this project did not write. Two routes a browser element must load directly - a stored picture and a spoken reply - accept a browser's own same-origin signal instead, which still refuses a program with `curl`
- Every outbound request passes one check that refuses any host but the shipped provider, and refuses plain `http` to it as well, before a connection is opened. The list is pinned to the shipped address rather than the configured one, and `127.0.0.1` is not on it: your own machine takes the same deliberate opt-in as any other address. With a proxy configured it reads the destination rather than the first hop, so the proxy cannot be used to reach elsewhere
- The app window refuses to navigate off its own origin. It has no address bar, so a page loaded there would wear Elysium's frame with nothing visible to contradict it

**At rest:** the database file is genuine SQLCipher ciphertext - without the
passphrase it does not open as SQLite at all. Images are stored as encrypted
blobs INSIDE that database (v0.6) - both the ones you attach and the ones a
model generates, through the same validate-and-re-encode pipeline - and the API
key + proxy URL are sealed in it too; served images carry `Cache-Control: no-store` so the
browser keeps no plaintext copies. The scrypt salt and verifier (`salt.bin`,
`verifier.bin`) sit beside the DB by design (they are not secrets, but never
publish them). `vault.recovery` is a second copy of the salt and its cost
settings, and holds no verifier: deleting or corrupting `salt.bin` alone used
to destroy the vault permanently even with the right passphrase, and this is
what survives that. The one-time migration from an older plaintext
database leaves a plaintext backup on purpose - Settings > Security lists it
and deletes it (overwriting first) once you are satisfied the move worked.
Three things are deliberately outside the vault: spoken replies, written as
plain `.wav` under the data folder and wiped at every lock, launch and
shutdown; the reference clip and transcript kept for a voice model that
CLONES, which nothing purges; and the chat wallpaper, which lives in browser
storage.

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
.venv\Scripts\Activate.ps1
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
.venv\Scripts\Activate.ps1
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
- `$env:ELYSIUM_SELFTEST = "1"; .\Elysium.exe` runs a headless boot check (exit 0 = OK)
- Two environment variables tune the notebook's background reader, and one of
  them raises what it may spend: `ELYSIUM_NOTEBOOK_EVERY_TURNS` (how many new
  messages before it runs, default 20) and `ELYSIUM_NOTEBOOK_DAILY_CALLS` (the
  daily ceiling, default 60)
- On launch the app **clears the `WEBVIEW2_*` environment variables** rather
  than adding to them. Anything already there is discarded and logged by name,
  never by value: those variables can hand the browser engine a debugging
  port, a redirected profile folder, or a different browser binary, and any
  program running as you can set them with one `setx`
- `ELYSIUM_ALLOW_BASE_URL_OVERRIDE=1` is needed to point the app anywhere
  other than the shipped provider, **including at your own machine**
- `ELYSIUM_SCREEN_PRIVACY=1` arms screen-capture hiding at launch. The
  ordinary way to turn it on is the Settings > Security checkbox, which is
  stored in the vault; this variable is the way in for somebody who wants it
  armed before the vault is unlocked
- `ELYSIUM_PER_MONITOR_DPI=0` turns off per-monitor DPI awareness, which is
  the way out if the window renders wrongly on a mixed-DPI setup
- `ELYSIUM_SKIP_LEGACY_MIGRATION=1` skips the one-time migration of secrets
  out of the Windows Credential Manager
- **If you deploy WebView2 in fixed-version mode, Elysium will not see it.**
  On launch the app clears every `WEBVIEW2_*` variable, including
  `WEBVIEW2_BROWSER_EXECUTABLE_FOLDER`, which is Microsoft's supported way to
  point an app at a fixed-version runtime folder. That is deliberate, because
  the same variable is how a program running as you would hand Elysium a
  browser binary of its choosing, and the app cannot tell the two apart. The
  cost is real and lands on locked-down or offline machines: install the
  Evergreen runtime, or run Elysium from source where you control the launch
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
docs/             frontend_contract.md and AUDIT_2026_07_CLOSURE.md only;
                  the rest of this folder is working notes and is gitignored,
                  so a clone does not have them and nothing here links to them
Elysium.exe       packaged desktop build (see Desktop build below)
```

A per-file tree used to live here and was wrong within weeks: eight backend
modules and a whole frontend voice/ directory had appeared without it
noticing. The directories above are stable; `git ls-files` is accurate.

## API Endpoints

All endpoints are under `/api/v1` (except `GET /healthz`, which lives at the
root). While the vault is locked, every data route answers `423 Locked`; only
the `/vault/*` routes and `/healthz` pass.

The full list lives in **[docs/frontend_contract.md](docs/frontend_contract.md)**
and is kept honest by a test: `test_every_route_the_app_serves_appears_in_the_contract`
fails the build if the app grows a route the contract does not mention.

That test compares **paths**, in one direction. It does not check that every
error code a route can return is written down, and an audit found the contract
missing several - so the error codes there are maintained by hand and the three
places they must agree (route, `shared/error_catalogue.json`,
`errorMessages.ts`) are what the build actually enforces.

A hand-maintained copy used to sit here and had drifted by eight routes,
which is the argument for one list rather than two.

## Verification

### Backend (pytest)

```powershell
cd backend
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest tests -q   # TestClient regression suite
```

The `tests/` suite covers the completion/regenerate flows (including the
provider-failure and abort paths), the vault lifecycle (migration, rekey,
recovery, the 423 gate), and attachments against an in-memory keyring and a
faked provider. `tests/mock_provider.py` is a stdlib-only OpenRouter stand-in
(with real word-by-word SSE framing) for end-to-end smoke testing with zero
network egress:

```powershell
.venv\Scripts\python tests\mock_provider.py            # terminal 1 (port 9797)
$env:ELYSIUM_ALLOW_BASE_URL_OVERRIDE = "1"             # terminal 2
$env:OPENROUTER_BASE_URL = "http://127.0.0.1:9797/api/v1"
uvicorn main:app --host 127.0.0.1 --port 8787
```

Twelve legacy `verify_*.py` scripts were deleted on 17 August 2026, because
they had stopped being checks. Two could not be imported at all after the
migration that moved secrets out of their reach, one had been declared dead in
writing months earlier, and across the rest seven separate assertions had gone
stale in the other direction: they claimed the app served eighteen routes, that
a message carried five keys, that the character and chat routes were GET-only.
Those would have reported failures on a correct app. Everything the twelve
still covered is in the test suite. Three tools stay,
because each does something no test can: `verify_hygiene.py` (the source gate
the commit hook runs), `verify_image_output.py` (a live request with your own
key) and `verify_tts_latency.py` (measures real hardware).

### Frontend

```powershell
cd frontend
npm test                          # full suite
npm test -- src/test/static-safety.test.ts   # static privacy checks
npm run typecheck                 # tsc strict - app + test configs
```

## Known Limitations (v1.1.5)

- **Plaintext migration backup** - upgrading an older unencrypted database keeps a plaintext `app.db.plain.bak-<timestamp>` copy next to the vault, deliberately: if the move had verified wrong it is the only copy left. Settings > Security shows it on every visit and removes it on request
- **A second copy after an interrupted move** - if the one-time migration is cut off midway it can leave a complete ENCRYPTED copy beside the vault. Settings > Security shows it, and offers to delete it only when it opens with your current passphrase; a copy it cannot open may belong to an older one, so the app refuses to remove it
- **The vault locks itself after 5 minutes idle** - Settings > Security changes the delay or turns it off. Idle means nothing in flight and nothing finished recently, so a chat reply that is still streaming holds it open however long it takes; a background note extraction does not, and the lock cancels its planning loop but waits up to five seconds for a reply that has already arrived. Locking also unloads the voice model and gives the GPU memory back
- **UI preferences are not encrypted** - type size, bubble solidity, the wallpaper image and how it is framed, last-open ids and the sampling parameters persist in the desktop app's local WebView profile (no chat content). The model you last picked used to sit there and no longer does: a model id like `author/slug` is a name a person reads on screen, so it moved into the vault. An install that already has the old plaintext copy has it deleted from that profile on the next launch, rather than merely being stopped from writing a newer one
- **No local/offline models** - OpenRouter only
- **No PDF/file upload** - images are supported (vision models); documents are not
- **Pictures in replies are not verified against a live model yet** - the whole path is built and covered by tests, but Elysium's privacy routing is strict enough that it is not certain any provider will accept the request. `backend/verify/verify_image_output.py` answers that with your own key in one run: whether a provider will answer at all under the policy, and whether it returns the picture inline (usable) or as a link (refused). Until you run it, treat the feature as untested against the real thing
- **A model that returns a link cannot be used for pictures** - the reply is kept, the picture is not, and a note says so. Fetching it would mean a second place your data goes
- **Pictures in replies are shown, not remembered by the model** - the model does not see its own drawing again on the next turn. That is deliberate; making it possible is a separate feature with a real token cost
- **An interrupted uploads migration can leave a stale snapshot of the whole vault** - `app.db.premigrate.bak` is a complete encrypted copy taken before the migration touches anything, kept whenever a pass does not finish cleanly. It opens with your current passphrase, so it is not exposed to anyone else - the problem is staleness: a message you delete afterward keeps living inside this frozen copy. Settings > Security now shows it and offers to delete it, the same as the plaintext migration backup, and a passphrase change re-keys it rather than leaving it under the old one. A copy that will not open with this vault's key is moved to `app.db.premigrate.bak.unreadable-<timestamp>` instead of deleted, because it may be the only copy of an older vault - that one still has no screen and no button, and only a vault reset or deleting the data folder removes it - a later clean migration pass discards just the readable snapshot, never this one
- **Resetting the vault destroys everything it can and cannot be undone** - the lock screen's "Forgot your passphrase?" flow (`POST /vault/reset`) is the only answer to a lost passphrase, because there is still no way to recover one. Confirmed by typing an exact phrase, it deletes the database with its journal siblings (`-wal`, `-shm`, `-journal`) and every backup family beside it (plaintext copies, the encrypted copies an interrupted migration orphaned and their own journal siblings, rotation snapshots, and the premigrate family - the snapshot itself, the half-written `.partial` a crash can leave mid-write, and the moved-aside unreadable copy), the empty database stub left by a recovery, the uploads folder, saved voice references and cached speech, the desktop app's browser profile, any leftover OS-keyring entries, and the app's own trail beside the vault: `elysium.log`, its rotated `elysium.log.1` and the `port` file. Then it reopens on the first-run setup screen. The log is in that list because it carries chat and note ids, so leaving it would keep a plaintext record of which chats had notes after the vault holding them was destroyed. It does not touch the downloaded voice engine, its runtimes or its install caches, which are software rather than your data. One family is conditional and it is worth knowing which: the shelved identity files (`salt.bin`, `verifier.bin`, `kdf.json`, `vault.recovery`, every `.bak-*` of each, and any leftover `.new`) go only once `app.db` is confirmed gone, and are held back untouched if it survives - destroying the recipe for a key while the file it opens is still on disk would leave a vault nobody could open again, not even with the right passphrase, so locked and intact and retryable is the better failure. Everything else still runs in that case, because none of it is the recipe for anything. It refuses outright while the vault is unlocked, on purpose: a confirmation phrase stops an accident, not someone reaching over your shoulder while a conversation is open, so this door exists only on the locked screen. The route does not exist outside the installed app: it answers 404 unless this is the packaged build AND the launch token gate is armed, so a development checkout has no reset door at all. Inside the packaged app it needs no further proof of identity beyond that launch token, so anyone who can present it can wipe the vault without ever knowing the passphrase; that is the same "any code running as this user" boundary the rest of the vault already accepts, not a new one. It is also not a forensic eraser: every file goes through the same overwrite-then-delete this app uses everywhere else, which defeats an undelete tool, but it cannot reach a copy the OS page file made of something once decrypted, a filesystem shadow copy taken earlier, or the original blocks an SSD's wear-levelling kept readable to firmware-level recovery after the logical overwrite. Full-disk encryption is the actual answer to that class, and it is yours to turn on, not this app's to fake. A file held open elsewhere can survive: the route returns `{"ok": false, "left": [...]}` (still HTTP 200) rather than pretending the wipe finished, and the lock screen names exactly what is left when that happens
- **The notebook's daily spend record is permanent** - the per-day ledger of calls, tokens and cost is what makes the sixty-a-day ceiling survive a restart, so nothing prunes it and deleting every chat you have does not touch it
- **The vault does not shrink** - deleting an image-heavy chat frees the space inside the database file but does not give it back to the disk
- **Privacy routing cannot be relaxed** - strict ZDR is always enforced; there is no compatibility mode and no toggle in the UI
- **No multi-branch chat** - linear conversation with per-message variants; the latest reply can be regenerated and continued from any variant, while older variant groups are view-only (browsable but the conversation always continues from their active take); delete-forward or edit-a-message to rewind
- **One window per data folder** - a second launch no longer opens a second window. It raises the window already open and stops before it touches the vault. Prevented rather than merely discouraged, because locking is per process: two windows each held their own copy of the vault key, so locking one left the other with the database fully decryptable. Two windows on two different data folders (`ELYSIUM_DATA_DIR`) still run side by side, since they share no key
- **Voice needs a one-time engine setup** - the speech engines are multi-GB and are not bundled in the exe; Settings › Voice installs one on request. An NVIDIA GPU is required to run them
- **One voice model at a time** - the card holds one; loading another unloads the first
- **Voice is production quality in English only** - the shipped engines speak Turkish with a heavy foreign accent rather than not at all, which is the worse failure of the two: nothing errors, the reply is simply read out wrong. Cloning from a native Turkish reference clip helps and does not fix it
- **Generated audio is session-only** - locking the vault or closing the app deletes the cached speech, the next launch clears whatever a crash or a kill left behind, and anything older than half an hour is cleared as the next reply is spoken
- **No speech recognition** - no shipped engine can listen to a reference clip and write out its words; type them in yourself. The control only appears for an engine that declares the ability
- **A voice reference still sits outside the vault, and only its folder name is hidden** - the directory a cloning voice lives in used to be named after the voice, so listing `voice\refs` read out what you had called each one, with no passphrase and nothing to unlock. That name is now an opaque hash and the listing gives nothing away. Nothing else changed: the clip, its transcript and the label inside that folder's `voice.json` are still ordinary plaintext files, readable by anything running as you, and they survive every lock, relaunch and shutdown. Only a vault reset or deleting the data folder removes them. Moving them into the vault is decided and not built
- **The notebook's reader spends your own credits, in the background** - it is off until you choose a model, capped at sixty calls a day (counted in the vault, so a restart does not reset it), and it never blocks a message. But it is a second thing sending your conversation to a provider, on a timer, while you are reading something else. The Notes tab shows every call it made and every one it refused
- **Automatic acceptance is ON by default** - a note the model wrote goes into the prompt without being reviewed. The panel announces it once and offers Undo; turn "Keep suggestions without asking" off in the Notes tab if you would rather approve each one. A chat opened from an **imported** character card always requires approval, whatever that switch says
- **A note can be marked as coming from the model's own words** - each note records whose sentence its quote was lifted from, and a note quoted out of the model's own reply says so on its face. That is the one thing no checker can supply for you: the app verifies that a note matches its quote, which says nothing at all when the model is quoting itself. The mark is a label, not a filter - such a note still goes in, because at the rate this actually happens a queue of them would be almost entirely correct notes and nobody would read it
- **Notes are written in English** - the reader is a small, cheap model and reads and writes English far better than Turkish. What you actually said is kept verbatim and shown under the English note so the paraphrase can be checked, but the note itself is not in your language
- **The note reader can be caught by a hostile message** - a note is text a model reads, and this app defends it with structural fences and a random per-request tag rather than by trusting the model. That stops a message forging a section boundary; it does not make a model immune to being talked to. Do not treat the notebook as a security boundary
- **A note extraction in flight is lost when the window closes** - the packaged app has no shutdown path (the process ends with the window), so an extraction that was mid-request is simply gone. The call was already sent, so it was already paid for: the app records that it happened, moves past that stretch of messages and does **not** send it again. The cost is that those particular messages are never read for notes; the alternative was paying twice for the same words every time you closed the window, which is what it used to do. The Notes tab counts these separately from ordinary failures, so a run that vanished does not look like a quiet week
- **`on_violation` is stored and not enforced** - a limit's "what should happen if this is crossed" is recorded and currently only told to the model. Prompt instructions are not controls, and this one is not pretending to be
- **Screen privacy is Windows-only and not a guarantee** - it uses the OS flag that excludes a window from capture. It stops the ordinary screenshot, recording and screen-share paths, not every possible way a screen can be read, and it does nothing while the vault is locked
- **Closing the accessibility tree costs screen readers, and changing it needs a restart** - with `ELYSIUM_ACCESSIBILITY_PRIVACY` at its default, assistive technology cannot read this app. There is no partial setting: it is the whole tree or none of it. Anyone who needs a screen reader has to set the variable to `0`, which also means accepting that any program running as them can read the conversation as text. The variable is read once, when the browser process is created, and WebView2 offers no way to change it afterwards, so setting it while Elysium is running does nothing at all - not on the next unlock, not on the next chat. That is the opposite of the screen-capture switch, which is a call on a window that already exists and so can follow the vault lock precisely. `false`, `no`, `off` and an empty value all leave the tree closed; only exactly `0` opens it. It also only covers the packaged desktop app, since it is an argument to Elysium's own browser process and not to a browser you opened yourself against the dev server

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Cannot reach the server | Ensure backend is running at `127.0.0.1:8787` |
| The notebook stopped suggesting notes | Open the Notes tab. It says which: no model chosen, today's sixty calls used, the reader paused after repeated failures, or the reader stopped and is waiting for "Try again now" |
| "The notebook has used its calls for today" | The daily ceiling did its job. It resets on the local day boundary; nothing was lost, and the messages it has not read yet stay unread rather than being skipped |
| The background reader has stopped | Repeated failures stopped it on purpose. Fix the cause (usually the API key or the model), then press "Try again now" in the Notes tab - it does not need a restart |
| A note appeared that I did not write | The model wrote it. The panel announces it once with an Undo; to be asked every time, turn off "Keep suggestions without asking" in the Notes tab |
| A limit is not being obeyed | Limits are told to the model, not enforced in code. Check "Use my global limits here" in the Notes tab if a chat is ignoring your global set - and see [SECURITY.md](SECURITY.md) on what a prompt instruction can and cannot do |
| Sending fails with a context error in one chat | Too many PINNED notes: pins are exempt from trimming, so enough of them fill the budget. Unpin a few in the Notes tab |
| CORS error / blank page | Open at `http://127.0.0.1:5173`, not `http://localhost:5173` |
| Wrong passphrase | There is no recovery - the passphrase is the key. Try again, or see "Forgot passphrase" below |
| Forgot passphrase | Data in the vault is unrecoverable by design. On the lock screen, "Forgot your passphrase?" wipes the vault and starts over once you type the confirmation phrase - the honest alternative to recovery, not a substitute for it. See Known Limitations for exactly what it destroys and what it cannot reach |
| Desktop app shows nothing | Install the WebView2 runtime (the app links it), then check `%LOCALAPPDATA%\Elysium\elysium.log` |
| API key not set | Configure OpenRouter API key in Settings |
| Authentication failed | API key is invalid or expired - update in Settings |
| Proxy required but not configured | Set proxy URL in Settings, or disable `proxy_required` |
| Model unavailable / ZDR error | Model doesn't support zero-data-retention - try a different model |
| Frontend tests fail | Run `npm install` then `npm test` from the `frontend/` directory |
