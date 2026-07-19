"""main.py - FastAPI application entry point.

Startup sequence:
  The server starts LOCKED; schema bootstrap and migrations run at UNLOCK
  time (routers/vault.py:_bootstrap_unlocked). Secrets live in the encrypted
  DB (E5), so there is no OS-keyring startup check anymore.

Shutdown:
  await close_client()         - cleanly close the shared httpx client.

Binding:
  Must be started with --host 127.0.0.1 to enforce localhost-only access.
  0.0.0.0 is never used.

CORS:
  allow_origins = ["http://127.0.0.1:5173"] only.
  No wildcard origins.

Routers are added phase by phase. Only GET /healthz is live in Phase 1.
"""

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import vault_state
from config import FRONTEND_ORIGIN
from network_client import close_client
from vault_state import VaultLockedError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────────
    # The server starts LOCKED: the database is passphrase-encrypted, so
    # schema bootstrap, the one-time legacy-keyring/uploads migrations, and
    # the staged-upload purge all run at UNLOCK time
    # (routers/vault.py:_bootstrap_unlocked), not here.
    logger.info("Startup: vault locked - waiting for passphrase.")

    yield

    # ── Shutdown ─────────────────────────────────────────────────────────────
    logger.info("Shutdown: closing HTTP client.")
    await close_client()


app = FastAPI(
    title="Elysium API",
    version="1.0.0",
    lifespan=lifespan,
    # No Swagger/ReDoc pages: they load their JS/CSS from a CDN, and this app
    # must make zero non-OpenRouter network requests. /openapi.json remains
    # (schema only, no data) for local tooling.
    docs_url=None,
    redoc_url=None,
)


# ── Vault gate ────────────────────────────────────────────────────────────────
# While locked, every data route answers 423 Locked; only /vault/* (the way
# in), /healthz (outside /api/v1) and CORS preflights pass. Registered BEFORE
# CORSMiddleware on purpose: Starlette wraps later-added middleware OUTSIDE
# earlier ones, so CORS ends up outermost and 423 responses still carry CORS
# headers (otherwise the frontend would see an opaque CORS failure instead
# of a catchable 423).
@app.middleware("http")
async def vault_gate(request: Request, call_next):
    path = request.url.path
    # ".." exclusion: without it "/api/v1/vault/../chats" would satisfy the
    # prefix test and skip the gate. (Starlette's router matches templates
    # literally so no data handler matches such a path today - this keeps the
    # gate's allow-set equal to the real vault routes instead of relying on
    # that downstream behavior.)
    is_vault_route = (
        path == "/api/v1/vault"
        or (path.startswith("/api/v1/vault/") and ".." not in path)
    )
    if (
        not vault_state.is_unlocked()
        and path.startswith("/api/v1")
        and not is_vault_route
        and request.method != "OPTIONS"
    ):
        return JSONResponse({"detail": "vault_locked"}, status_code=423)
    return await call_next(request)


# Backstop for the gate's check-then-act window: if the vault locks AFTER the
# middleware check but BEFORE a handler's get_db(), VaultLockedError would
# otherwise become a 500 + traceback. Map it to the same 423 the gate returns.
@app.exception_handler(VaultLockedError)
async def _vault_locked_handler(request: Request, exc: VaultLockedError):
    return JSONResponse({"detail": "vault_locked"}, status_code=423)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_methods=["GET", "POST", "DELETE", "PATCH"],
    allow_headers=["Content-Type"],
    allow_credentials=False,
)

# DNS-rebinding shield: CORS alone cannot stop a hostile page whose domain
# re-resolves to 127.0.0.1 - the browser then treats this API as same-origin.
# Rejecting foreign Host headers closes that path for the unauthenticated
# local API (chats, personas - exactly the data that must never leave).
app.add_middleware(
    TrustedHostMiddleware,
    # "testserver" is Starlette's TestClient host; a single-label name is not
    # routable on the public internet, so it adds no rebinding surface.
    allowed_hosts=["127.0.0.1", "localhost", "testserver"],
)

# ── Routers (uncommented phase by phase) ─────────────────────────────────────
#
# Phase 2 - Settings (active)
from routers import settings as settings_router
app.include_router(settings_router.router, prefix="/api/v1")
#
# Phase 3 - Characters (active)
from routers import characters as characters_router
app.include_router(characters_router.router, prefix="/api/v1")
#
# Phase 5B - Completions (active)
from routers import completions as completions_router
app.include_router(completions_router.router, prefix="/api/v1")
#
# Phase 4 - Chats (active)
from routers import chats as chats_router
app.include_router(chats_router.router, prefix="/api/v1")
#
# Phase 5A - Models (active)
from routers import models_router
app.include_router(models_router.router, prefix="/api/v1")
#
# Part C - Personas (active)
from routers import personas as personas_router
app.include_router(personas_router.router, prefix="/api/v1")
#
# Part H - Image attachments (active)
from routers import uploads as uploads_router
app.include_router(uploads_router.router, prefix="/api/v1")
#
# Part K - Vault (full-DB passphrase encryption; active)
from routers import vault as vault_router
app.include_router(vault_router.router, prefix="/api/v1")


# ── Phase 1 liveness probe ────────────────────────────────────────────────────

@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict:
    """Simple liveness probe. Returns 200 if the server is up."""
    return {"ok": True}


# ── Frontend (packaged desktop app) ───────────────────────────────────────────
# When a built frontend exists, this single process serves BOTH the API and
# the SPA (the packaged app has no separate Vite server). Mounted LAST so the
# API routers and /healthz match first; reachable while the vault is locked
# because the lock screen IS the frontend - only /api/v1 data routes are gated.
def _frontend_dist() -> Path | None:
    if getattr(sys, "frozen", False):
        candidate = Path(getattr(sys, "_MEIPASS", ".")) / "frontend_dist"
    else:
        candidate = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    return candidate if candidate.is_dir() else None


_dist = _frontend_dist()
if _dist is not None:
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="frontend")
    logger.info("Serving frontend from %s", _dist)
