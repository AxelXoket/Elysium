# Elysium Frontend

React 19 + TypeScript (strict) + Vite client for [Elysium](../README.md), a privacy-first AI character chat app. The frontend talks **only** to the local FastAPI backend at `http://127.0.0.1:8787` - it never contacts OpenRouter directly, never emits an `Authorization` header, and keeps secrets out of browser storage. Completions stream token-by-token over SSE, relayed through the backend.

## Commands

| Command | What it does |
|---------|--------------|
| `npm run dev` | Start the Vite dev server at `http://127.0.0.1:5173` |
| `npm test` | Run the vitest suite once (`vitest --run`) |
| `npm run typecheck` | `tsc --noEmit` against both `tsconfig.app.json` and `tsconfig.test.json` |
| `npm run build` | Typecheck the app config, then `vite build` |
| `npx eslint .` | Lint (also available as `npm run lint`) |

Open the app at exactly `http://127.0.0.1:5173` - backend CORS intentionally rejects `http://localhost:5173`.

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `VITE_API_BASE_URL` | `http://127.0.0.1:8787/api/v1` | Backend API base URL override (see `.env.example`) |

## Layout

```
src/
├── app/              App entry, providers, stale-selection reconciliation
├── components/
│   ├── chat/         ChatCanvas, Composer, MessageList, MessageBubble
│   ├── characters/   Character list + create/import/edit dialogs
│   ├── chats/        Chat create dialog
│   ├── errors/       ErrorToastStack (global safe-error toasts)
│   ├── generation/   Generation settings dialog + settings context
│   ├── models/       Model panel (catalogue, search, selection)
│   ├── persona/      Persona panel
│   ├── preview/      Active context preview card
│   ├── settings/     API key + proxy sections
│   ├── sidebar/      Sidebar layout
│   └── layout/, motion/, ui/   Shell, animation, and base UI primitives
├── lib/
│   ├── api/          REST client + SSE stream client (stream.ts)
│   ├── query/        TanStack Query hooks (all resources)
│   ├── schemas/      Zod schemas + inferred types (the wire contract)
│   ├── generation/   Param filtering, clamping, payload builders
│   ├── errors/       Safe error parser, code→message mapper, error store
│   ├── store/        Zustand UI store
│   └── chat/, characters/, models/, personas/, preview/, theme/
└── test/
    ├── components/   Focused suites per feature slice
    ├── helpers/      streamMocks.ts - SSE-aware fetch stub for streaming tests
    ├── mocks/        API mocks + shared fixtures
    ├── static-safety.test.ts   Static privacy checks (no direct provider calls, no secret storage)
    └── fe0-contract.test.ts    API contract shape tests
```

## Contract of record

The backend API surface, streaming frames, and privacy rules this client must follow are specified in [`../docs/frontend_contract.md`](../docs/frontend_contract.md). Project-wide setup, architecture, and the privacy contract live in the [root README](../README.md).
