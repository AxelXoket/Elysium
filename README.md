# Elysium

A privacy-first, localhost-only AI character chat client powered by OpenRouter.  
Elysium routes all model traffic through a local FastAPI backend — the frontend never contacts OpenRouter directly.

---

## Features

- **Character System** — Create, import (Character Card V2 JSON), and manage characters with full field support (system prompt, description, personality, scenario, example dialogue, post-history instruction)
- **Persona System** — Create and switch AI personas that are injected as a system block into every completion request
- **OpenRouter Integration** — Browse and select from the full OpenRouter model catalogue; generation parameters (temperature, top\_p, top\_k, max\_tokens, stop, seed, etc.) are validated and forwarded
- **Context Budget** — App-level `context_budget_tokens` controls history trimming; oldest messages are dropped to fit the budget — never forwarded to OpenRouter
- **Message Lifecycle** — Send, regenerate (latest assistant message), delete (target + all following), and clear chat
- **Privacy by Design** — ZDR, data\_collection=deny, allow\_fallbacks=false are hardcoded in the backend and cannot be overridden by the frontend
- **OS Keyring** — API key and proxy URL live in the OS keyring (Windows Credential Manager / macOS Keychain / libsecret) — never in the database, never sent to the frontend
- **Strict CORS** — Backend accepts requests from `http://127.0.0.1:5173` only; no wildcard origins

---

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
│   Chat       ──┘                                         │
└─────────────────────────────┬───────────────────────────┘
                              │ http only, 127.0.0.1
                              ▼
┌─────────────────────────────────────────────────────────┐
│              Backend (FastAPI + Uvicorn)                  │
│                  http://127.0.0.1:8787                   │
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────┐ │
│  │ settings │  │characters│  │  chats/  │  │personas │ │
│  │          │  │          │  │ complete │  │         │ │
│  └──────────┘  └──────────┘  └──────────┘  └─────────┘ │
│                                                          │
│  SQLite (WAL)  ·  OS Keyring  ·  httpx (trust_env=False) │
│                                                          │
│  PROVIDER_POLICY (hardcoded, immutable):                 │
│    zdr=true · data_collection=deny · allow_fallbacks=false│
└──────────────────────────────┬──────────────────────────┘
                               │ HTTPS · Authorization header only
                               ▼
                    ┌─────────────────────┐
                    │  OpenRouter API      │
                    │  api.openrouter.ai   │
                    └─────────────────────┘
```

---

## Privacy Contract

Elysium enforces strict privacy routing on every OpenRouter request:

| Field | Value | Overridable? |
|-------|-------|--------------|
| `provider.zdr` | `true` | ❌ Never |
| `provider.data_collection` | `"deny"` | ❌ Never |
| `provider.allow_fallbacks` | `false` | ❌ Never |

Additional guarantees:

- `context_budget_tokens` is **never** forwarded to OpenRouter — it is an app-level history-trimming budget only
- `raw_json`, `avatar_path`, `image_url`, `tools`, `tool_choice`, `response_format`, streaming — **never** sent
- API key lives in the OS keyring; it is never stored in the database, never returned by any endpoint, never logged
- Raw upstream OpenRouter error bodies are never forwarded to the client
- Persona descriptions and character fields are never logged
- Browser storage (localStorage, sessionStorage, IndexedDB) holds only UI preferences — never messages, API keys, or proxy URLs

These guarantees are enforced by 261 automated regression tests and 20 static privacy grep checks that run on every verification pass.

> **Note on proxy\_required:** If `proxy_required=false` (default), the app connects to OpenRouter directly without a proxy. Your IP may be visible to OpenRouter. Set `proxy_required=true` to enforce proxy-only traffic.

---

## Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.13 · FastAPI · Uvicorn |
| Database | SQLite (WAL mode, raw `sqlite3`) |
| HTTP client | httpx with SOCKS proxy support (`trust_env=False`) |
| Secrets | OS keyring (Windows Credential Manager / macOS Keychain / libsecret) |
| Frontend | React 19 · Vite · TypeScript · TanStack Query · Zustand · Zod |

---

## Repository Layout

```
elysium/
├── backend/
│   ├── routers/          API route handlers
│   │   ├── settings.py   API key, proxy, health
│   │   ├── characters.py Character CRUD + import
│   │   ├── chats.py      Chat + message management
│   │   ├── completions.py POST /complete + /regenerate
│   │   └── personas.py   Persona CRUD + select
│   ├── config.py         App-wide constants + PROVIDER_POLICY
│   ├── database.py       SQLite init + connection helper
│   ├── keyring_service.py OS keyring abstraction
│   ├── network_client.py Shared httpx client
│   ├── openrouter.py     OpenRouter API client + model cache
│   ├── proxy_health.py   Proxy health check with TTL cache
│   ├── main.py           FastAPI app + CORS + router wiring
│   ├── requirements.txt
│   └── verify_*.py       Automated regression tests (261 checks)
├── frontend/             React + Vite frontend
├── docs/
│   ├── frontend_contract.md   Backend→Frontend API contract
│   └── backend_implementation_plan_opus.md
├── start_backend.bat     Windows quick-start script
├── .gitignore
└── README.md
```

---

## Setup

### Backend

```bash
cd backend
py -3.13 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8787
```

Or use the included quick-start script:

```bat
start_backend.bat
```

### Frontend

```bash
cd frontend
npm install
npm run dev        # starts at http://127.0.0.1:5173
```

> **Never use `0.0.0.0`** — both services must bind to `127.0.0.1` only.

---

## Getting Started

1. **Start the backend** at `127.0.0.1:8787`
2. **Start the frontend** at `127.0.0.1:5173`
3. **Open the app** at exactly `http://127.0.0.1:5173`
   - Do **not** use `http://localhost:5173` — intentionally rejected by the strict CORS policy
4. **Set your OpenRouter API key** in Settings. Stored in the OS keyring, never sent to the frontend
5. **Import or create a character**, select a model, and start chatting

---

## API Endpoints

All endpoints are under `/api/v1`.

| Method | Path | Description |
|--------|------|-------------|
| GET | /healthz | Liveness probe |
| GET | /settings | Current config state (no secrets) |
| POST | /settings/api-key | Store + validate API key |
| DELETE | /settings/api-key | Remove API key |
| POST | /settings/proxy | Store proxy config |
| DELETE | /settings/proxy | Remove proxy config |
| GET | /settings/proxy/health | Proxy health status |
| GET | /characters | List all characters |
| POST | /characters | Create character |
| POST | /characters/import | Import JSON character card |
| GET | /characters/{id} | Get single character |
| PATCH | /characters/{id} | Edit character (partial update) |
| DELETE | /characters/{id} | Delete character + cascade |
| GET | /chats | List all chats |
| POST | /chats | Create chat session |
| GET | /chats/{id} | Get single chat |
| GET | /chats/{id}/messages | List messages |
| POST | /chats/{id}/complete | Send message, get completion |
| DELETE | /chats/{id} | Delete chat + messages |
| POST | /chats/{id}/clear | Clear messages, keep chat |
| DELETE | /chats/{id}/messages/{msg\_id} | Delete target + following messages |
| POST | /chats/{id}/messages/{msg\_id}/regenerate | Regenerate latest assistant message |
| GET | /personas | List personas (includes is\_active) |
| POST | /personas | Create persona |
| PATCH | /personas/{id} | Edit persona |
| DELETE | /personas/{id} | Delete persona |
| POST | /personas/{id}/select | Set active persona |
| GET | /models/openrouter | List OpenRouter models |

---

## Verification

```bash
cd backend

# Full regression suite (261 checks + 20 privacy grep checks)
.venv\Scripts\python verify_elysium_full.py

# Individual verify scripts
.venv\Scripts\python verify_part_a.py    #  8/8   baseline, contract, error format
.venv\Scripts\python verify_part_b.py    # 17/17  API key validation, context_budget_tokens
.venv\Scripts\python verify_part_c.py    # 32/32  personas lifecycle, is_active, injection
.venv\Scripts\python verify_part_d.py    # 14/14  character PATCH, DELETE cascade
.venv\Scripts\python verify_part_e.py    # 40/40  message delete, clear, regenerate lifecycle
.venv\Scripts\python verify_phase5b.py   # 150/150 completion, gen params, provider policy,
                                         #         context trimming, privacy, CORS

# Legacy phase scripts (pre-refactor)
.venv\Scripts\python verify_phase1.py    # liveness + DB
.venv\Scripts\python verify_phase2.py    # settings, API key, proxy
.venv\Scripts\python verify_phase3.py    # character management
.venv\Scripts\python verify_phase4.py    # chat + message management
.venv\Scripts\python verify_phase5a.py   # model listing + caching
```

---

## Known Limitations (MVP v0.1)

- **No streaming** — completion is non-streaming; full response appears after OpenRouter returns it
- **No local/offline models** — OpenRouter only
- **No file/image/PDF upload** — text-only characters and messages
- **No Compatibility Mode** — strict ZDR/privacy routing is always enforced; some OpenRouter models may be unavailable
- **No ZDR toggle** — privacy settings cannot be relaxed in the UI
- **No multi-branch chat** — linear conversation only; delete-forward to rewind
- **Character avatar upload not enabled** — avatar\_path field exists in DB but file upload UI is not implemented

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Cannot reach the server | Ensure backend is running at `127.0.0.1:8787` |
| CORS error / blank page | Open at `http://127.0.0.1:5173`, not `http://localhost:5173` |
| API key not set | Configure OpenRouter API key in Settings |
| Authentication failed | API key is invalid or expired — update in Settings |
| Proxy required but not configured | Set proxy URL in Settings, or disable `proxy_required` |
| Proxy unreachable | Check proxy config and ensure the proxy is running |
| Model unavailable / ZDR error | Model does not support zero-data-retention routing — select a different model |
| Unexpected response errors | Check backend logs for sanitized error codes; raw upstream bodies are never shown |
