<p align="center">
  <h1 align="center">Elysium</h1>
  <p align="center">
    <strong>Privacy-first, localhost-only AI character chat client powered by OpenRouter</strong>
  </p>
  <p align="center">
    <img src="https://img.shields.io/badge/python-3.13-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python">
    <img src="https://img.shields.io/badge/version-1.1.6-brightgreen?style=flat-square" alt="Version">
    <img src="https://img.shields.io/badge/privacy-ZDR_enforced-brightgreen?style=flat-square" alt="Privacy">
    <img src="https://img.shields.io/badge/at--rest-SQLCipher_vault-brightgreen?style=flat-square" alt="Encryption">
    <img src="https://img.shields.io/badge/streaming-SSE-brightgreen?style=flat-square" alt="Streaming">
    <img src="https://img.shields.io/badge/frontend_tests-2218_passed-success?style=flat-square" alt="Frontend Tests">
    <img src="https://img.shields.io/badge/frontend-React_19-61DAFB?style=flat-square&logo=react&logoColor=white" alt="React">
  </p>
  <p align="center">
    <img src="assets/elysium_banner.png" alt="Elysium" width="820">
  </p>
</p>

---

Elysium is a privacy-first AI character chat client that routes **all model traffic through a local FastAPI backend**. The frontend never contacts OpenRouter directly. Your entire chat database - messages, characters, personas, attached images, your notebook and limits, and your API key - is **passphrase-encrypted at rest (SQLCipher)**, strict ZDR privacy routing is enforced on every request, and raw upstream error bodies are never exposed to the client. It runs as a dev server pair or as a **packaged Windows desktop app** (`Elysium.exe`).

## What's new

**v1.1.6**, 30 August 2026, is the shipped release and the `Elysium.exe` in this
repository. Mostly repair, but not only: the side panels collapse for the first
time, the notebook's "Try it on this chat" is gone, and the voice settings are
reorganised. Nothing on disk changes, so upgrading is replacing the exe.

Work has landed since and none of it raised the version, so 1.1.6 is still the
number everywhere it is written down. That work sits under **Since 1.1.6** in
**[CHANGELOG.md](CHANGELOG.md)**, which is where unreleased entries wait until a
version move turns that block into the next release's notes. The rest of this
file describes the tree rather than the exe, so where the two differ, that block
is what moved. Keeping the history in one file is what stops this one growing a
section per release and never losing one.

## Features

- **Passphrase Vault** - The whole database (characters, chats, messages, personas, settings, attached image bytes, and your OpenRouter API key + proxy URL) is a SQLCipher-encrypted file. The app starts locked; a passphrase unlocks it (scrypt-derived raw key, held only in RAM). Change the passphrase from the Security tab; closing the desktop app locks the vault. There is no way to recover a forgotten passphrase, and the lock screen's "Forgot your passphrase?" link is the only way out of one: it wipes the vault and starts over. Some things sit outside it - spoken audio, the reference clip you record for a voice model that CLONES and what one engine derives from it, the identity files beside the database, and the chat wallpaper in the browser profile's IndexedDB. [SECURITY.md](SECURITY.md) lists every one of them, and what a reset does and does not reach
- **Character System** - Create, import (Character Card V2 JSON), and manage characters with full field support (system prompt, description, personality, scenario, example dialogue, post-history instruction)
- **Persona System** - Create and switch AI personas that are injected as a system block into every completion request
- **Streaming Responses** - Token-by-token SSE streaming with a live cursor and a Stop control. However a reply ends early - you press Stop, the provider drops out mid-sentence, the model hits its own token limit - the text you have already read is kept and marked with a "Truncated" label, so a cut-short reply never reads as one that simply finished. Only a send that produced nothing rolls back and gives your draft back
- **Response Variants** - Regenerate keeps every take: swipe between variants in a carousel and pick which one the conversation continues from
- **OpenRouter Integration** - Browse and select from the full OpenRouter model catalogue. Generation parameters (temperature, top\_p, top\_k, min\_p, top\_a, frequency\_penalty, presence\_penalty, repetition\_penalty, max\_tokens, seed) are validated, filtered against what the model declares, and forwarded
- **Context Budget** - App-level `context_budget_tokens` controls history trimming; oldest messages are dropped to fit the budget - never forwarded to OpenRouter as a provider field
- **Message Lifecycle** - Send (streaming + optimistic UI), regenerate with variant history, delete (target + all following), clear chat, and rename chats inline from the sidebar
- **Names fill themselves in** - an empty name box is never refused and never deletes what was there. Chats, characters, personas, voices, limits and the proxy label each take `<prefix> <n>` with the smallest free number, decided on the server so the answer is the same whichever door you came through. Delete "New Chat 2" and that name is available again
- **Copy Out** - Every message carries a copy button that puts the original text on the clipboard, asterisks and all, not the styled version on screen; text selection is on window-wide, so dragging across a reply and pressing Ctrl+C works too, with timestamps and variant counters kept out of the selection. What happens to a copy once it leaves is Windows' business - [SECURITY.md](SECURITY.md) covers Clipboard History and its sync
- **Stop Sequences** - Up to 4 stop sequences (with `\n` support) managed as chips in Generation Settings; always forwarded to the provider, mirroring the backend rule
- **Safeword** - one phrase that stops a turn, and the only limit in this app enforced in code rather than asked of the model. In the message you are sending it refuses the turn outright: nothing goes to the provider and nothing is stored. The same word ANYWHERE ELSE in an outgoing body - older history, a note, a character card, a stop sequence - is cut out instead, in the one place all three senders meet, so a word written once does not make the chat unusable. Model ids, provider policy and response schemas are scanned and left alone. Neither half rewrites the vault: the word stays in the stored message and on screen
- **Image Attachments** - Attach up to 4 images (PNG/JPEG/WebP) per message to vision-capable models; drag-in/paste/pick, thumbnail strip, full-size lightbox. The attach UI is gated by the model's image modality, images are downscaled and content-addressed server-side, and the backend builds the provider payload (the frontend never constructs image URLs)
- **Reading & Ambience Settings** - In-app settings for message font size and line height, `*narration*` styling, an optional chat wallpaper with framing, zoom, contrast/tint controls and adaptive text, message-bubble solidity, and a living WebGL mist backdrop (with a static fallback)
- **Notebook** - a per-chat list of what the story has established, sent with every message. You write the notes, or let a small model of your choosing read the last turns and suggest them - off until you pick one, because it is your API key. Notes are never deleted behind your back: a superseded one retires, and one that does not fit the ceiling stays in the panel saying why. What the model writes lives in its own weaker block, after the history, and says who wrote it
- **Limits** - a separate list of what you never want written, `everywhere` or `this chat`. Never expire, never merge, and **never trimmed to make room**: if they do not fit the model's context, the app refuses to send rather than quietly dropping them. Any chat can be told to ignore the global set
- **A daily ceiling on the notebook's spending** - sixty calls, counted in the database so a restart does not reset it, blocked before the request rather than warned about after. Failures stop the reader, and where they stop it depends on the fault: something this chat's own material caused (a card the moderation refuses, a reply that will not parse) stops that chat alone, while something no chat caused and every chat would repeat (429, 5xx, 401, 402, a timeout, a refused proxy) stops all of them. The Notes tab shows runs, calls used today, credits spent, why anything was skipped, and which chats are stopped, with the lifetime call count and lifetime credits beside today's
- **Screen privacy** - Settings > Security can hide the window from screenshots, screen recording and screen sharing. Off by default, stored in the vault, and not applied while the vault is locked. A layer, not a guarantee
- **The conversation is closed to the accessibility tree** - the embedded browser publishes the whole page to Windows' accessibility layer as text, and an unprivileged program running as the same user can read the chat title, the character name and the message bodies straight out of it. Screen-capture exclusion does not touch that path: it hides pixels and this is text. Elysium starts its browser with that tree switched off, and this is the one protection here that is ON when nobody asked for it. The cost is not hidden: while it is on, a screen reader cannot read this app at all. `setx ELYSIUM_ACCESSIBILITY_PRIVACY 0` and a restart is the whole way out - an environment variable rather than a checkbox, because somebody who needs a screen reader cannot navigate to a setting inside an app they cannot read, and the switch has to be reachable before the vault is unlocked. Only exactly `0` turns it off, and the launch log names the state that took effect. [SECURITY.md](SECURITY.md) has the measurement behind this
- **Sidebar Navigation** - Persona strip with a switcher, client-side character search, and New Chat / New Character docks. Both side panels collapse to a handle, and each side is remembered separately across a restart
- **Active Context Preview + live context meter** - Local-only collapsible card in the Models tab showing what the next request will include (model, persona, character, message count, generation params, context budget) plus a live "≈ used / capacity tokens" gauge on the selected model, which the notebook's size feeds into; approximate estimates, never the exact provider payload
- **Error Toast System** - Centralized safe error notifications over the chat canvas; max 5 visible and extras queued. A toast lives at least 4.5 s and longer the more it has to say, up to 14 s, and the countdown pauses while you hover it, focus it from the keyboard, or leave the tab
- **Privacy by Design** - the provider policy is hardcoded backend-side and cannot be overridden from anywhere; see [Privacy Contract](#privacy-contract) for the exact fields and the full list of what is and is not sent
- **Sealed Secrets** - API key and proxy URL live inside the encrypted vault (unreadable while locked); a one-time migration moves them out of the OS keyring and deletes the old entries - never sent to the frontend. Settings > Security can test the stored key against OpenRouter without you retyping it, and "rejected" and "could not reach OpenRouter" are reported as two different answers, since only one of them means the key is bad
- **Strict CORS + Host allowlist** - running from source, the backend accepts browser requests from `http://127.0.0.1:5173` and no other origin. The packaged exe allows none at all: it serves the page itself, so nothing legitimate is cross-origin there and every mutating request passes a same-origin shield instead. Foreign `Host` headers are rejected either way (DNS-rebinding shield)
- **Locks itself when idle** - after 5 minutes of doing nothing the vault closes: the key leaves memory, the voice model is unloaded and the GPU memory comes back. Change the delay or turn it off in Settings > Security. A reply that is still streaming counts as activity for as long as it runs; a background note extraction deliberately does NOT, and the lock cancels its planning loop
- **Takes its own folder back** - at launch Elysium checks whether other accounts on this PC can reach its data folder and removes that access, naming what it removed in the log. Your database is encrypted, but `salt.bin` and `verifier.bin` beside it are what an offline passphrase attack needs. This is the one change that persists after the app closes; [SECURITY.md](SECURITY.md) says how to undo it
- **Desktop App** - PyInstaller build (one-folder for development, a single ~28 MB exe for release) with a native window (pywebview + WebView2); the exe serves the built frontend same-origin on a loopback port it remembers between launches (a fresh one only when that port is taken), and the vault key never leaves RAM, so closing the window is the lock

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
│   Voice      │                                           │
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
│  ┌────────────────────────────────────────────────────┐│
│  │ tts (voices, speech) · tts_runtime (engine setup)  ││
│  └────────────────────────────────────────────────────┘│
│                                                          │
│  SQLCipher vault (DB + images + secrets, WAL) · httpx     │
│  (trust_env=False) · 423 vault gate while locked         │
│                                                          │
│  PROVIDER_POLICY (hardcoded, immutable):                 │
│    zdr=true · data_collection=deny · allow_fallbacks=true│
│    failover: same model, only endpoints both filters pass│
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
frontend on one loopback port (same-origin, no CORS in play), shown in a native
WebView2 window. The voice engine runs as a separate local process and never
touches OpenRouter.

## Privacy Contract

Elysium enforces strict privacy routing on every OpenRouter request:

| Field | Value | Overridable? |
|-------|-------|--------------|
| `provider.zdr` | `true` | ❌ Never |
| `provider.data_collection` | `"deny"` | ❌ Never |
| `provider.allow_fallbacks` | `true` | ❌ Never |

Every completion request body carries all three side by side, from the chat routes and the notebook's reader alike. OpenRouter documents `zdr` and `data_collection` as filters: it routes only to endpoints with a zero data retention policy, and only to providers that do not collect data. It documents `allow_fallbacks` as permission to use a backup provider when the one it picked is unavailable, taken from the candidates that remain. That means a backup still has to pass both filters, and failover relaxes neither. Model fallback is a separate `models` parameter, and no request Elysium builds carries one, so failover does not swap the model.

Additional guarantees:

- `context_budget_tokens` is **never** forwarded to OpenRouter - app-level history trimming only
- `raw_json`, `avatar_path`, `tools`, `tool_choice` - **never** sent. `response_format` is sent by exactly one path, the notebook's background reader, and only ever as the same fixed schema defined in this repository: the only request that carries it is that reader's own, and nothing the frontend sends can add it to any request at all. That reader does send your conversation - it is the one thing here that does, on a timer, and the Known Limitations section says so at length. This line used to say nothing sent it at all, which stopped being true in v1.1.5 when that reader shipped. `image_url` parts are built server-side **only** for images the user explicitly attached (vision models); the frontend never constructs them. `reasoning` is sent only when the cached model list says the model takes it: to switch reasoning off, or, for a model that requires it, at its lowest effort with the reasoning text excluded. Temperature, top_p, top_k and repetition_penalty are not sent unless you set them.
- Raw upstream OpenRouter error bodies are never forwarded to the client - safe mapped codes only, on the streaming path as well as the plain one. Streaming otherwise uses the same hardcoded provider policy, relaying deltas untouched
- Models that search the web on their own are refused before anything is sent: any `:online` variant, `openrouter/auto`, any `@preset/...` id, and model ids naming `sonar`, `-search` or `deep-research`. The check sits in the one sender every request passes through, so it covers the chat routes and the notebook's background reader alike.
- Every system message starts with one line of hex, a keyed hash of the chat id under a key held only in memory. It carries no instruction and not the chat id, and the key is dropped at every lock.
- API key is sealed inside the encrypted vault (unreachable while locked); never returned by any endpoint, never logged. A message that contains your stored API key is refused before it is stored or sent, and the key is cut out of every outgoing request body wherever else it appears (history, card, modules, notes).
- Browser storage holds only UI preferences - never messages, personas, characters, API keys, or proxy URLs
- Frontend never emits an `Authorization` header - all provider auth happens backend-side
- Logs carry numeric ids, counts and status codes - never message content, passphrases, key material, or a name you read on screen. One gap is measured rather than claimed away, because a promise nobody has counted is not a promise: crash handlers write the exception's own message, which can hold whatever the code was holding when it failed, and there are fifty such places. A scanner reads every logging call in the shipped backend, that number is written down, and it cannot grow without something going red. The gap this sentence used to name second - a voice created before the folder names were made opaque carrying the label you typed into the log when it was deleted or listed - is CLOSED: those lines now write twelve characters of a keyed hash instead, and the scanner's content ledger is empty
- Every response carries a Content-Security-Policy plus `X-Content-Type-Options: nosniff` and `X-Frame-Options: DENY`, including the 423/403/400 refusals that never reach a route. The policy exists for containment rather than for XSS: the same local origin serves the whole API, so `default-src 'self'` means nothing running there has anywhere to send data. The packaged build additionally sends `Cross-Origin-Resource-Policy: same-origin`, which stops a remote page that guesses the port from probing your attachments through an `<img>` tag
- A stored image is served only if its recorded type is one the app itself produced (PNG/JPEG/WebP); anything else is refused rather than handed to the browser
- The key is derived with scrypt at OWASP's current floor (N=2^17, r=8, p=1). The parameters are recorded per vault, so an older vault keeps opening under the ones it was made with and is re-derived to the current ones the next time it is unlocked - the one moment the passphrase exists in memory and a re-key is possible at all
- A passphrase must be at least 12 characters and must not be a repeated fragment, a keyboard walk, or one of the phrases any guessing attempt starts with. There is no rate limit behind this vault - somebody with the folder guesses offline - so length and variety are what matter, and composition rules (one capital, one digit, one symbol) are deliberately NOT imposed
- Locking overwrites the key in memory rather than dropping the reference. Optionally the vault locks itself after a chosen idle period; a chat request still in flight counts as activity, so a streamed reply is never cut short, but a background note extraction does not count as activity, and the lock cancels its planning loop. It does NOT cancel a reply that has already arrived: the lock waits up to five seconds for that one to be written, so a note can land in the seconds after you press Lock
- The desktop window is given a secret at launch and every API request carries it. Loopback is not a permission boundary: without this, any program running as your user could read the whole conversation over HTTP while Elysium is open, which is exactly when the vault is unlocked. The secret travels in the URL fragment (never sent to a server, never logged), is read once and stripped from the address, and is kept in memory rather than in browser storage. It is also withheld from every subprocess Elysium starts, since the voice engine and the installer both run code this project did not write. There is no route it does not cover. Two of them used to be reachable without it - a stored picture and a spoken reply, which a browser element loads directly and cannot attach a header to - and the gate took the browser's own `Sec-Fetch-Site: same-origin` signal in the token's place. That refuses a hostile PAGE, which cannot forge the header, but not a local program, which sets it with one flag; sequential ids then made every stored picture reachable by counting upward. Elysium now fetches both routes itself, with the token, and shows the result from a `blob:` URL, so the exemption is gone rather than narrowed
- Every outbound request THIS APP MAKES passes one check that refuses any host but the shipped provider, and refuses plain `http` to it as well, before a connection is opened. The check sits on the HTTP client every one of those requests is built with. Two things sit outside that client, and both are named here rather than in a footnote. The voice-engine installer opens its own connection to fetch a Python package manager, and the package manager it then runs reaches PyPI and the model host as separate processes; nothing on that path carries a word of your conversation, and none of it runs unless you ask for a voice. The launcher's own readiness probe is the second: on every start it asks the server it just started whether it is up, over plain `http` to `127.0.0.1` and nothing else, with empty request bodies. The list the check enforces is pinned to the shipped address rather than the configured one, and `127.0.0.1` is not on it - so any request THROUGH the client to your own machine takes the same deliberate opt-in as any other address, and the launcher's probe reaches loopback by not being one of those requests. With a proxy configured it reads the destination rather than the first hop, so the proxy cannot be used to reach elsewhere
- The app window refuses to navigate off its own origin. It has no address bar, so a page loaded there would wear Elysium's frame with nothing visible to contradict it

**At rest:** the database file is genuine SQLCipher ciphertext - without the
passphrase it does not open as SQLite at all. Images are stored as encrypted
blobs INSIDE that database (v0.6) - both the ones you attach and the ones a
model generates, through the same validate-and-re-encode pipeline - and the API
key + proxy URL are sealed in it too; served images carry `Cache-Control: no-store` so the
browser keeps no plaintext copies. Prompt modules you import (their text, their
names, the file name and the SillyTavern preset fields kept with them) are
stored only inside the database. The file is read in memory and never written
to disk, and fields that can hold a secret are removed before anything is kept. The scrypt salt and verifier (`salt.bin`,
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

> **Note on `proxy_required`:** If set to `false` (default), the app connects to OpenRouter directly. Your IP may be visible to OpenRouter. Set `proxy_required=true` and every request to the provider goes through your proxy or does not go at all - completions, `/models`, and the voice installer's multi-gigabyte downloads alike. The switch fails CLOSED, and it covers the whole install rather than its first step: both of the installer's ways onto the network ask one gate first, so if the setting cannot be read - the app locked itself while you were away, say - nothing starts at all, neither the installer's own download nor the multi-gigabyte package downloads it drives, and the setup stops and asks you to unlock instead of proceeding without the proxy. The installer's child processes are handed an environment with the ambient `HTTP_PROXY`/`NO_PROXY`/index variables stripped, so a proxy exported in your shell can neither capture the traffic nor be used to skip yours.
>
> What it does NOT cover: the embedded browser window. WebView2 loads this app's own files from the local server and is not routed through the proxy. It makes no request to any other host - crash reporting is blocked at the filesystem level, and every API response carries a no-cache directive - `no-store` on ordinary responses, and the streaming ones keep the `no-cache` that SSE requires - but "proxy-only" is a statement about the app's outbound requests, not about the browser control.

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

- **Python 3.13**. The voice engine is a separate matter: it provisions its own
  interpreter, pinned at 3.12, into its own folder
- **Node.js `^20.19.0 || ^22.13.0 || >=24`** with npm. Not a bare floor: the
  range is the intersection of what the tools declare. vite asks for
  `^20.19.0 || >=22.12.0`; eslint and jsdom both ask for
  `^20.19.0 || ^22.13.0 || >=24`, which is the stricter of the two. So 21.x is
  refused by everything, and 23.x is accepted by vite but refused by the
  linter and the test DOM. The same range is declared in
  `frontend/package.json` `engines`, and `frontend/src/test/install-docs.test.ts`
  binds these two together
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

`frontend/README.md` covers what belongs to that directory alone: the command
table, `VITE_API_BASE_URL` and `.env.example`, and the two install orderings
that are easy to get wrong.

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
pyinstaller elysium_onefile.spec   # one FILE, ~28 MB
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
- Five environment variables tune the notebook's background reader, and four
  of them raise what it may spend: `ELYSIUM_NOTEBOOK_EVERY_TURNS` (how many new
  messages before it runs, default 20), `ELYSIUM_NOTEBOOK_DAILY_CALLS` (the
  daily ceiling, default 60), and the three that decide how much text one
  extraction prompt may carry - `ELYSIUM_NOTEBOOK_CARD_CHARS` (the character
  card, default 4000), `ELYSIUM_NOTEBOOK_EXISTING_CHARS` (the notes already
  kept, default 6000) and `ELYSIUM_NOTEBOOK_TURNS_CHARS` (the messages being
  read, default 12000). The daily ceiling bounds how many calls are made, not
  how large they are, so raising the last three raises what a single call costs
- On launch the app **clears the nine `WEBVIEW2_*` environment variables**
  rather than adding to them. Anything already there is discarded and logged
  by name, never by value: those variables can hand the browser engine a
  debugging port, a redirected profile folder, or a different browser binary,
  and any program running as you can set them with one `setx`
- `ELYSIUM_ALLOW_BASE_URL_OVERRIDE=1` is needed to point the app anywhere
  other than the shipped provider, **including at your own machine**
- `ELYSIUM_SCREEN_PRIVACY=1` arms screen-capture hiding at launch. The
  ordinary way to turn it on is the Settings > Security checkbox, which is
  stored in the vault; this variable is the way in for somebody who wants it
  armed before the vault is unlocked
- `ELYSIUM_ACCESSIBILITY_PRIVACY=0` reopens the accessibility tree for a
  screen reader. It is read once, when the browser process is created, so it
  has to be set before launch, and only exactly `0` counts
- `ELYSIUM_PER_MONITOR_DPI=0` turns off per-monitor DPI awareness, which is
  the way out if the window renders wrongly on a mixed-DPI setup
- `ELYSIUM_SKIP_LEGACY_MIGRATION=1` skips the one-time migration of secrets
  out of the Windows Credential Manager
- `ELYSIUM_LAUNCH_TOKEN` is what the packaged launcher hands its own window,
  and setting it arms the same gate on a hand-run `uvicorn`. Every API request
  then has to carry it
- **If you deploy WebView2 in fixed-version mode, Elysium will not see it.**
  On launch the app clears every `WEBVIEW2_*` variable, including
  `WEBVIEW2_BROWSER_EXECUTABLE_FOLDER`, which is Microsoft's supported way to
  point an app at a fixed-version runtime folder. That is deliberate, because
  the same variable is how a program running as you would hand Elysium a
  browser binary of its choosing, and the app cannot tell the two apart. The
  cost is real and lands on locked-down or offline machines: install the
  Evergreen runtime, or run Elysium from source where you control the launch
- Startup problems are logged to `%LOCALAPPDATA%\Elysium\elysium.log`, which
  the packaged app writes and a dev checkout does not

## Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.13 · FastAPI · Uvicorn |
| Database | SQLCipher (encrypted SQLite, WAL) via `sqlcipher3-wheels` · scrypt KDF |
| HTTP client | httpx with SOCKS proxy support (`trust_env=False`) |
| Secrets | Sealed in the encrypted vault DB (one-time migration out of the OS keyring) |
| Frontend | React 19 · Vite · TypeScript (strict) · TanStack Query v5 · Zustand · Zod v4 |
| Frontend UI | Base UI primitives · Lucide icons · Tailwind CSS v4 · motion |
| Voice | Local engine in its own Python 3.12 environment, installed when you ask for it |
| Desktop | pywebview (WebView2) · PyInstaller (one-folder to develop, one-file to ship) |

## Repository Layout

```
backend/           FastAPI app: routers/, tts/, vault + hardening modules, tests/
frontend/          React SPA: src/components, src/lib, src/test - and its own README
shared/            JSON contracts both sides import, error_catalogue.json among them
docs/              frontend_contract.md
assets/            the banner at the top of this file
CHANGELOG.md       every release, and the work already in the tree
SECURITY.md        what is protected, what is not, and what persists
Elysium.exe        packaged desktop build (see Desktop build above)
start_backend.bat  the backend quick start above, as one double-click
```

That is the whole of what is tracked at the top level. A per-file tree used to
live here and was wrong within weeks, so the rule replaced it: `git ls-files`
is the accurate answer inside these folders, and everything the app writes is
gitignored rather than listed.

## API Endpoints

All endpoints are under `/api/v1` (except `GET /healthz`, which lives at the
root). While the vault is locked, every data route answers `423 Locked`; only
the `/vault/*` routes and `/healthz` pass.

The full list lives in **[docs/frontend_contract.md](docs/frontend_contract.md)**,
and `test_every_route_the_app_serves_appears_in_the_contract` in
`backend/tests/test_release_sync.py` fails the build if the app grows a route
the contract does not mention. That test compares **paths**, in one direction:
it does not check that every error code a route can return is written down. The
three places an error code must agree - the route, `shared/error_catalogue.json`
and `frontend/src/lib/errors/errorMessages.ts` - are what the build enforces
instead.

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

`backend/verify/` holds what a test cannot do. `run_gates.py` drives the two
commit lanes: pre-commit runs the hygiene scanner over the index and is meant
to stay under a couple of seconds, while pre-push runs the full backend suite,
the typecheck, the linter, the whole vitest run, and a real PyInstaller build
with its selftest, building outside the tree so verification never quietly
replaces the shipped exe. The hooks themselves are versioned at
`backend/verify/hooks/`, because `.git/hooks` is neither tracked nor cloned;
install each with `cp backend/verify/hooks/<name> .git/hooks/<name>`, and
`verify_hygiene.py` reports when what is installed differs from what is
tracked. A hook exports `GIT_DIR` and friends, and a child that inherited them
would run its git commands against this repository wherever it ran them, so
every process the lanes start gets them stripped (`git_env.py`), and every
git call in the suite goes through `backend/tests/_git_env.py`, which strips
them too and keeps the machine's own git config out of the tests.
Five tools are run by hand, each doing something no test can:
`verify_hygiene.py` (the source scanner, which is also the pre-commit lane),
`verify_image_output.py` (a live request with your own key),
`verify_tts_latency.py` (measures real hardware), `verify_tts_resolve.py`
(asks the package index whether the three voice engine freezes still
install, with the exact command the installer runs) and
`verify_tts_install.py` (builds every voice engine through the app's own
installer into a throwaway folder, then loads a model and speaks with each).

### Frontend

```powershell
cd frontend
npm test                          # full suite
npm test -- src/test/static-safety.test.ts   # static privacy checks
npm run typecheck                 # tsc strict - app + test configs
```

## Known Limitations

- **Copies beside the vault** - four situations leave a second file next to `app.db`, and Settings > Security shows each one with its own notice. A plaintext `app.db.plain.bak-<timestamp>` is kept on purpose when an older unencrypted database is migrated: if the move had verified wrong it is the only copy left. An interrupted migration can strand a complete ENCRYPTED copy. `app.db.premigrate.bak` is a full encrypted snapshot taken before an uploads migration touches anything, and it goes stale - a message you delete afterward keeps living inside it. A passphrase change leaves a rotation snapshot. Each is offered for deletion only when it opens with your current passphrase, because one it cannot open may belong to an older vault; a premigrate copy that will not open is moved aside as `app.db.premigrate.bak.unreadable-<timestamp>`, which has a notice but no button, and only a vault reset removes it. A passphrase change re-keys these copies rather than leaving them under the old one
- **The vault locks itself after 5 minutes idle** - Settings > Security changes the delay or turns it off. Idle means nothing in flight and nothing finished recently, so a chat reply that is still streaming holds it open however long it takes; a background note extraction does not, and the lock cancels its planning loop but waits up to five seconds for a reply that has already arrived. Locking also unloads the voice model and gives the GPU memory back
- **UI preferences are not encrypted** - type size, bubble solidity, the wallpaper image and how it is framed, last-open ids and the sampling parameters persist in the desktop app's local WebView profile. No chat content is there, and the model you last picked no longer is either: a model id like `author/slug` is a name you read on screen, so it moved into the vault, and an install holding the old plaintext copy has it deleted on the next launch
- **No local/offline models** - OpenRouter only
- **No PDF/file upload** - images are supported (vision models); documents are not
- **Pictures in replies are not verified against a live model yet** - the whole path is built and covered by tests, but Elysium's privacy routing is strict enough that it is not certain any provider will accept the request. `backend/verify/verify_image_output.py` answers that with your own key in one run: whether a provider will answer at all under the policy, and whether it returns the picture inline (usable) or as a link (refused). Until you run it, treat the feature as untested against the real thing
- **A model that returns a link cannot be used for pictures** - the reply is kept, the picture is not, and a note says so. Fetching it would mean a second place your data goes
- **Pictures in replies are shown, not remembered by the model** - the model does not see its own drawing again on the next turn. That is deliberate; making it possible is a separate feature with a real token cost
- **Resetting the vault destroys everything it can and cannot be undone** - the lock screen's "Forgot your passphrase?" flow (`POST /vault/reset`) is the only answer to a lost passphrase. Confirmed by typing an exact phrase, it deletes the database, every backup family beside it, the uploads folder, saved voice references and cached speech, the browser profile, leftover OS-keyring entries, and the app's own trail (`elysium.log`, its rotated copy and the `port` file), then reopens on the first-run setup screen. It leaves the downloaded voice engine alone, which is software rather than your data. It refuses while the vault is unlocked, on purpose, and the route answers 404 outside the packaged app, so a development checkout has no reset door at all. It is not a forensic eraser and it cannot promise a file held open elsewhere went: the route reports `{"ok": false, "left": [...]}` rather than pretending. [SECURITY.md](SECURITY.md) has the whole list, the one family that is held back when the database survives, and what an SSD keeps anyway
- **The notebook's daily spend record is permanent** - the per-day ledger of calls, tokens and cost is what makes the sixty-a-day ceiling survive a restart, so nothing prunes it and deleting every chat you have does not touch it
- **The vault does not shrink** - deleting an image-heavy chat frees the space inside the database file but does not give it back to the disk
- **Privacy routing cannot be relaxed** - strict ZDR is always enforced; there is no compatibility mode and no toggle in the UI
- **No multi-branch chat** - linear conversation with per-message variants; the latest reply can be regenerated and continued from any variant, while older variant groups are view-only (browsable but the conversation always continues from their active take); delete-forward or edit-a-message to rewind
- **One window per data folder** - a second launch no longer opens a second window. It raises the window already open and stops before it touches the vault. Prevented rather than merely discouraged, because locking is per process: two windows each held their own copy of the vault key, so locking one left the other with the database fully decryptable. Two windows on two different data folders (`ELYSIUM_DATA_DIR`) still run side by side, since they share no key
- **Voice needs a one-time engine setup** - the speech engines are multi-GB and are not bundled in the exe; Settings > Voice installs one when you ask. An NVIDIA GPU is required to run them. With `proxy_required` on, the setup does not start at all unless a proxy can actually be read: a locked vault answers 409 rather than going out bare
- **One voice model at a time** - the card holds one; loading another unloads the first
- **Voice is production quality in English only** - the shipped engines speak Turkish with a heavy foreign accent rather than not at all, which is the worse failure of the two: nothing errors, the reply is simply read out wrong. Cloning from a native Turkish reference clip helps and does not fix it
- **Generated audio is session-only, and plaintext while it lasts** - spoken replies are ordinary `.wav` files under the data folder. Locking the vault or closing the app deletes them, the next launch clears whatever a crash or a kill left behind, and while the engine is loaded anything older than half an hour is swept on its health tick
- **No speech recognition** - no shipped engine can listen to a reference clip and write out its words; type them in yourself. The control only appears for an engine that declares the ability
- **A voice reference still sits outside the vault, and only its folder name is hidden** - the directory a cloning voice lives in is named with a keyed hash, so listing `voice\refs` gives nothing away. The clip, its transcript and the label inside that folder's `voice.json` are still ordinary plaintext files, readable by anything running as you, and they survive every lock, relaunch and shutdown. Only deleting the voice, deleting the folder, or resetting the vault removes them. [SECURITY.md](SECURITY.md) also names the voiceprint one engine derives from that clip
- **The notebook's reader spends your own credits, in the background** - it is off until you choose a model, capped at sixty calls a day, and it never blocks a message. But it is a second thing sending your conversation to a provider, on a timer, while you are reading something else. The Notes tab shows every call it made and every one it refused
- **Automatic acceptance is ON by default** - a note the model wrote goes into the prompt without being reviewed. The panel announces it once and offers Undo; turn "Keep suggestions without asking" off in the Notes tab if you would rather approve each one. A chat opened from an **imported** character card always requires approval, whatever that switch says, and the route that changes the setting refuses to lower that shield
- **A note can be marked as coming from the model's own words** - each note records whose sentence its quote was lifted from, and a note quoted out of the model's own reply says so on its face. That is the one thing no checker can supply for you: the app verifies that a note matches its quote, which says nothing at all when the model is quoting itself. The mark is a label, not a filter - such a note still goes in, because at the rate this actually happens a queue of them would be almost entirely correct notes and nobody would read it
- **Notes are written in English** - the reader is a small, cheap model and reads and writes English far better than Turkish. What you actually said is kept and shown under the English note so the paraphrase can be checked, word for word, cut at 240 characters. The note itself is not in your language
- **The note reader can be caught by a hostile message** - a note is text a model reads, and this app defends it with structural fences and a random per-request tag rather than by trusting the model. That stops a message forging a section boundary; it does not make a model immune to being talked to. Do not treat the notebook as a security boundary
- **A note extraction in flight is lost when the window closes** - the packaged app has no shutdown path (the process ends with the window), so an extraction that was mid-request is simply gone. The call was already sent, so it was already paid for: the app records that it happened, moves past that stretch of messages and does **not** send it again. The cost is that those particular messages are never read for notes; the alternative was paying twice for the same words every time you closed the window. The Notes tab counts these separately from ordinary failures, so a run that vanished does not look like a quiet week
- **`on_violation` is stored and not enforced** - a limit's "what should happen if this is crossed" is recorded and currently only told to the model. Prompt instructions are not controls, and this one is not pretending to be. The safeword is the exception in the other direction: it is the one limit enforced in code
- **Screen privacy is Windows-only and not a guarantee** - it uses the OS flag that excludes a window from capture. It stops the ordinary screenshot, recording and screen-share paths, not every possible way a screen can be read, and it does nothing while the vault is locked
- **Closing the accessibility tree costs screen readers, and changing it needs a restart** - with `ELYSIUM_ACCESSIBILITY_PRIVACY` at its default, assistive technology cannot read this app. There is no partial setting: it is the whole tree or none of it. Anyone who needs a screen reader has to set the variable to `0`, which also means accepting that any program running as them can read the conversation as text. The variable is read once, when the browser process is created, and WebView2 offers no way to change it afterwards, so setting it while Elysium is running does nothing at all. `false`, `no`, `off` and an empty value all leave the tree closed; only exactly `0` opens it. It also only covers the packaged desktop app, since it is an argument to Elysium's own browser process and not to a browser you opened yourself against the dev server

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Cannot reach the server | Ensure backend is running at `127.0.0.1:8787` |
| The notebook stopped suggesting notes | Open the Notes tab. It says which: no model chosen, today's sixty calls used, the reader paused after repeated failures, the reader stopped and is waiting for "Try again now", or this one conversation stopped while the rest keep running |
| "The notebook has used its calls for today" | The daily ceiling did its job. It resets on the local day boundary; nothing was lost, and the messages it has not read yet stay unread rather than being skipped |
| The background reader has stopped | Repeated failures stopped it on purpose. Fix the cause (usually the API key or the model), then press "Try again now" in the Notes tab - it does not need a restart |
| One chat stopped taking notes and the others did not | That chat's own material broke the call - a card the provider refuses, a reply that will not parse. Fix it, then press "Try this chat again" in the Notes tab; the button only appears while that chat is stopped |
| A note appeared that I did not write | The model wrote it. The panel announces it once with an Undo; to be asked every time, turn off "Keep suggestions without asking" in the Notes tab |
| A limit is not being obeyed | Limits are told to the model, not enforced in code. Check "Use my global limits here" in the Notes tab if a chat is ignoring your global set - and see [SECURITY.md](SECURITY.md) on what a prompt instruction can and cannot do |
| A message will not send and names the safeword | The safeword is in the message being sent, which is exactly what it is for. Change the message, or clear the word in the Notes tab. The same word in older history or a note does not block the turn - it is cut out of the request instead |
| Sending fails with a context error in one chat | Too many PINNED notes: pins are exempt from trimming, so enough of them fill the budget. Unpin a few in the Notes tab |
| Voice setup refuses to start | With `proxy_required` on, the installer will not go out without your proxy. Unlock the vault so the setting can be read, or configure the proxy in Settings |
| CORS error / blank page | Open at `http://127.0.0.1:5173`, not `http://localhost:5173` |
| Wrong passphrase | There is no recovery - the passphrase is the key. Try again, or see "Forgot passphrase" below |
| Forgot passphrase | Data in the vault is unrecoverable by design. On the lock screen, "Forgot your passphrase?" wipes the vault and starts over once you type the confirmation phrase - the honest alternative to recovery, not a substitute for it. See Known Limitations for what it destroys |
| Desktop app shows nothing | Install the WebView2 runtime (the app links it), then check `%LOCALAPPDATA%\Elysium\elysium.log` |
| API key not set | Configure OpenRouter API key in Settings |
| Authentication failed | API key is invalid or expired - update in Settings |
| Proxy required but not configured | Set proxy URL in Settings, or disable `proxy_required` |
| Model unavailable / ZDR error | Model doesn't support zero-data-retention - try a different model |
| Frontend tests fail | Run `npm install` then `npm test` from the `frontend/` directory |
