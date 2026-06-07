<p align="center">
  <h1 align="center">Elysium</h1>
  <p align="center">
    <strong>Privacy-first, localhost-only AI character chat client powered by OpenRouter</strong>
  </p>
  <p align="center">
    <img src="https://img.shields.io/badge/python-3.13-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python">
    <img src="https://img.shields.io/badge/license-MIT-blue?style=flat-square" alt="License">
    <img src="https://img.shields.io/badge/version-0.1.5-orange?style=flat-square" alt="Version">
    <img src="https://img.shields.io/badge/privacy-ZDR_enforced-brightgreen?style=flat-square" alt="Privacy">
    <img src="https://img.shields.io/badge/backend_tests-261_passed-success?style=flat-square" alt="Backend Tests">
    <img src="https://img.shields.io/badge/frontend_tests-417_passed-success?style=flat-square" alt="Frontend Tests">
    <img src="https://img.shields.io/badge/frontend-React_19-61DAFB?style=flat-square&logo=react&logoColor=white" alt="React">
  </p>
</p>

---

Elysium is a privacy-first AI character chat client that routes **all model traffic through a local FastAPI backend**. The frontend never contacts OpenRouter directly. API keys live in the OS keyring, strict ZDR privacy routing is enforced on every request, and raw upstream error bodies are never exposed to the client.

## Features

- **Character System** — Create, import (Character Card V2 JSON), and manage characters with full field support (system prompt, description, personality, scenario, example dialogue, post-history instruction)
- **Persona System** — Create and switch AI personas that are injected as a system block into every completion request
- **OpenRouter Integration** — Browse and select from the full OpenRouter model catalogue; generation parameters (temperature, top\_p, top\_k, max\_tokens, seed, repetition\_penalty) are validated, model-filtered, and forwarded
- **Context Budget** — App-level `context_budget_tokens` controls history trimming; oldest messages are dropped to fit the budget — never forwarded to OpenRouter as a provider field
- **Message Lifecycle** — Send (with optimistic UI), regenerate (latest assistant message), delete (target + all following), and clear chat
- **Error Toast System** — Centralized safe error notifications over the chat canvas; auto-dismiss after 4.5 s, max 5 visible, extras queued
- **Active Context Preview** — Local-only preview of what will be included in the next request (model, persona, character, message count, generation params, context budget); approximate, never the exact provider payload
- **Privacy by Design** — ZDR, data\_collection=deny, allow\_fallbacks=false are hardcoded in the backend and cannot be overridden
- **OS Keyring** — API key and proxy URL live in the OS keyring (Windows Credential Manager / macOS Keychain / libsecret) — never in the database, never sent to the frontend
- **Strict CORS** — Backend accepts requests from `http://127.0.0.1:5173` only; no wildcard origins

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
│  │          │  │          │  │ complete │  │         │ │
│  └──────────┘  └──────────┘  └──────────┘  └─────────┘ │
│                                                          │
│  SQLite (WAL)  ·  OS Keyring  ·  httpx (trust_env=False) │
│                                                          │
│  PROVIDER_POLICY (hardcoded, immutable):                 │
│    zdr=true · data_collection=deny · allow_fallbacks=false│
└──────────────────────────────┬──────────────────────────┘
                               │ HTTPS · Authorization only
                               ▼
                    ┌─────────────────────┐
                    │  OpenRouter API      │
                    │  api.openrouter.ai   │
                    └─────────────────────┘
```

## Privacy Contract

Elysium enforces strict privacy routing on every OpenRouter request:

| Field | Value | Overridable? |
|-------|-------|--------------|
| `provider.zdr` | `true` | ❌ Never |
| `provider.data_collection` | `"deny"` | ❌ Never |
| `provider.allow_fallbacks` | `false` | ❌ Never |

Additional guarantees:

- `context_budget_tokens` is **never** forwarded to OpenRouter — app-level history trimming only
- `raw_json`, `avatar_path`, `image_url`, `tools`, `tool_choice`, `response_format`, streaming — **never** sent
- API key lives in the OS keyring; never stored in the database, never returned by any endpoint, never logged
- Raw upstream OpenRouter error bodies are never forwarded to the client — safe mapped messages only
- Browser storage holds only UI preferences — never messages, personas, characters, API keys, or proxy URLs
- Frontend never emits an `Authorization` header — all provider auth happens backend-side

> **Note on `proxy_required`:** If set to `false` (default), the app connects to OpenRouter directly. Your IP may be visible to OpenRouter. Set `proxy_required=true` to enforce proxy-only traffic.

## Quick Start

### Prerequisites

- **Python 3.13** (3.12+ compatible)
- **Node.js 20+** with npm
- **OS keyring** — Windows Credential Manager, macOS Keychain, or libsecret

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

> **Never use `0.0.0.0`** — both services must bind to `127.0.0.1` only.

### Getting Started

1. **Start the backend** at `127.0.0.1:8787`
2. **Start the frontend** at `127.0.0.1:5173`
3. **Open the app** at exactly `http://127.0.0.1:5173`
   - Do **not** use `http://localhost:5173` — intentionally rejected by CORS
4. **Set your OpenRouter API key** in Settings — stored in OS keyring, never sent to frontend
5. **Import or create a character**, select a model, and start chatting

## Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.13 · FastAPI · Uvicorn |
| Database | SQLite (WAL mode, raw `sqlite3`) |
| HTTP client | httpx with SOCKS proxy support (`trust_env=False`) |
| Secrets | OS keyring (Windows Credential Manager / macOS Keychain / libsecret) |
| Frontend | React 19 · Vite · TypeScript · TanStack Query v5 · Zustand · Zod v4 |
| Frontend UI | Radix UI primitives · Lucide icons · Tailwind CSS v4 |

## Repository Layout

```
elysium/
├── backend/
│   ├── routers/              API route handlers
│   │   ├── settings.py       API key, proxy, health
│   │   ├── characters.py     Character CRUD + import
│   │   ├── chats.py          Chat + message management
│   │   ├── completions.py    POST /complete + /regenerate
│   │   └── personas.py       Persona CRUD + select
│   ├── config.py             App-wide constants + PROVIDER_POLICY
│   ├── database.py           SQLite init + connection helper
│   ├── keyring_service.py    OS keyring abstraction
│   ├── network_client.py     Shared httpx client
│   ├── openrouter.py         OpenRouter API client + model cache
│   ├── proxy_health.py       Proxy health check with TTL cache
│   ├── main.py               FastAPI app + CORS + router wiring
│   └── verify_*.py           Backend regression suites (261 checks)
├── frontend/
│   └── src/
│       ├── app/              App entry, providers, routing
│       ├── components/
│       │   ├── chat/         ChatCanvas, Composer, MessageList, ThinkingBubble
│       │   ├── characters/   Character list, create/import dialogs
│       │   ├── chats/        Chat list
│       │   ├── errors/       ErrorToastStack (FE-1B)
│       │   ├── models/       Model panel
│       │   ├── persona/      Persona panel area
│       │   ├── settings/     ApiKeySection, ProxySection
│       │   └── sidebar/      Sidebar layout
│       ├── lib/
│       │   ├── api/          REST API client functions
│       │   ├── characters/   Character helpers (FE-6A)
│       │   ├── chat/         Chat action helpers (FE-5A)
│       │   ├── errors/       Error parser, mapper, store (FE-1A)
│       │   ├── generation/   Generation params helpers + payload builders (FE-4A)
│       │   ├── models/       Model metadata + modality helpers (FE-7A)
│       │   ├── personas/     Persona active/id helpers (FE-3A)
│       │   ├── preview/      Active context preview builder (FE-8A)
│       │   ├── query/        TanStack Query hooks (all resources)
│       │   ├── schemas/      Zod schemas + inferred types
│       │   └── store/        Zustand UI store
│       └── test/
│           ├── components/   20 focused test suites (417 tests total)
│           ├── static-safety.test.ts   20 static privacy checks
│           └── fe0-contract.test.ts    API contract shape tests
├── docs/
│   ├── frontend_contract.md              Frontend ↔ backend API contract
│   ├── elysium_frontend_implementation_roadmap.md
│   └── backend_implementation_plan_opus.md
├── start_backend.bat         Windows quick-start script
├── .gitignore
└── README.md
```

## API Endpoints

All endpoints are under `/api/v1`.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/healthz` | Liveness probe |
| `GET` | `/settings` | Current config state (no secrets) |
| `POST` | `/settings/api-key` | Store + validate API key |
| `DELETE` | `/settings/api-key` | Remove API key |
| `POST` | `/settings/proxy` | Store proxy config |
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
| `GET` | `/chats/{id}/messages` | List messages |
| `POST` | `/chats/{id}/complete` | Send message, get completion |
| `DELETE` | `/chats/{id}` | Delete chat + messages |
| `POST` | `/chats/{id}/clear` | Clear messages, keep chat |
| `DELETE` | `/chats/{id}/messages/{msg_id}` | Delete target + all following messages |
| `POST` | `/chats/{id}/messages/{msg_id}/regenerate` | Regenerate latest assistant message |
| `GET` | `/personas` | List personas (includes `is_active`) |
| `POST` | `/personas` | Create persona |
| `PATCH` | `/personas/{id}` | Edit persona |
| `DELETE` | `/personas/{id}` | Delete persona |
| `POST` | `/personas/{id}/select` | Set active persona |
| `GET` | `/models/openrouter` | List OpenRouter models (cached) |

## Frontend Logic Foundation (v0.1.5)

The frontend logic layer is complete and cleared for UI implementation. All slices are pure helpers — no UI components, no browser storage, no direct OpenRouter calls.

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

### Backend (261 checks)

```powershell
cd backend

# Full regression suite
.venv\Scripts\python verify_elysium_full.py

# Individual suites
.venv\Scripts\python verify_part_a.py     #   8/8   baseline, contract, error format
.venv\Scripts\python verify_part_b.py     #  17/17  API key validation, context_budget
.venv\Scripts\python verify_part_c.py     #  32/32  personas lifecycle, is_active
.venv\Scripts\python verify_part_d.py     #  14/14  character PATCH, DELETE cascade
.venv\Scripts\python verify_part_e.py     #  40/40  message delete, clear, regenerate
.venv\Scripts\python verify_phase5b.py    # 150/150 completion, gen params, privacy
```

### Frontend (417 tests)

```powershell
cd frontend
npm test                          # full suite — 417 tests, 21 files
npm test -- src/test/static-safety.test.ts   # 20 static privacy checks
npm run typecheck                 # TypeScript strict mode
```

## Known Limitations (v0.1.5)

- **No streaming** — full response appears after OpenRouter returns it
- **No local/offline models** — OpenRouter only
- **No file/image/PDF upload** — text-only characters and messages
- **No Compatibility Mode** — strict ZDR privacy routing always enforced
- **No ZDR toggle** — privacy settings cannot be relaxed in the UI
- **No multi-branch chat** — linear conversation only; delete-forward to rewind
- **UI panels pending** — Persona Panel, Generation Params Panel, Character Library, Model Panel, and Active Context Preview UI are logic-complete and awaiting Codex UI implementation (FE-3B through FE-8B)

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Cannot reach the server | Ensure backend is running at `127.0.0.1:8787` |
| CORS error / blank page | Open at `http://127.0.0.1:5173`, not `http://localhost:5173` |
| API key not set | Configure OpenRouter API key in Settings |
| Authentication failed | API key is invalid or expired — update in Settings |
| Proxy required but not configured | Set proxy URL in Settings, or disable `proxy_required` |
| Model unavailable / ZDR error | Model doesn't support zero-data-retention — try a different model |
| Frontend tests fail | Run `npm install` then `npm test` from the `frontend/` directory |
