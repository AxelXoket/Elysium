# Elysium Frontend

The React SPA. Setup, architecture and the privacy contract live in the
[root README](../README.md); this file covers only what is specific to this
directory.

React 19, TypeScript in strict mode, Vite. This client never talks to
OpenRouter. Every request goes to the local FastAPI backend, which holds the
key and relays completions back token by token over SSE. In development Vite
serves the page on `127.0.0.1:5173` and the backend answers on
`127.0.0.1:8787`. A production build is served by the backend process itself,
so it calls whatever origin it was loaded from.

## Commands

| Command | What it does |
|---------|--------------|
| `npm run dev` | Vite dev server on `http://127.0.0.1:5173` |
| `npm test` | The suite once (`vitest --run`) |
| `npm run test:watch` | The suite in watch mode |
| `npm run typecheck` | `tsc --noEmit` over `tsconfig.app.json` and `tsconfig.test.json` |
| `npm run lint` | `eslint .` |
| `npm run build` | Typecheck the app config, then `vite build` into `dist/` |

`package.json` carries the rest: `test:report` and `preview`.

Open the app at exactly `http://127.0.0.1:5173`. `http://localhost:5173` is
refused on purpose, because the backend's CORS allowlist holds that one origin
and nothing else.

## Configuration

`VITE_API_BASE_URL` overrides the API base. Left unset it resolves to
`http://127.0.0.1:8787/api/v1` in development and to the relative `/api/v1` in
a production build. See `.env.example` and `src/lib/api/base.ts`.

## Two orderings that are load-bearing

- `main.tsx` imports `./lib/zodJitless` before anything else. The flag it sets
  is read while the schema modules are still being evaluated, so a later
  import is a late import.
- `vite.config.ts` loads `src/test/sentinels.ts` ahead of `src/test/setup.ts`.
  The sentinels install the runtime traps for egress, storage, console,
  headers and payloads; a module that writes at import time walks past all of
  them if it is imported first.

Both read like tidy-up candidates. Neither is.

## Where things are

`src/components/` is grouped by feature slice, `src/lib/` by concern, and
`src/test/` mirrors both. There is no file tree here on purpose: the one in
the root README was wrong within weeks, and the one that used to sit in this
file went the same way. `git ls-files frontend/src` is accurate.

## Contract of record

The API surface, the streaming frames and the privacy rules this client has to
follow are specified in
[`../docs/frontend_contract.md`](../docs/frontend_contract.md).
