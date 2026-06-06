"""main.py — FastAPI application entry point.

Startup sequence:
  1. verify_keyring_backend()  — fail closed if OS keyring is insecure.
  2. init_db()                 — idempotent schema bootstrap.

Shutdown:
  await close_client()         — cleanly close the shared httpx client.

Binding:
  Must be started with --host 127.0.0.1 to enforce localhost-only access.
  0.0.0.0 is never used.

CORS:
  allow_origins = ["http://127.0.0.1:5173"] only.
  No wildcard origins.

Routers are added phase by phase. Only GET /healthz is live in Phase 1.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import FRONTEND_ORIGIN
from database import init_db
from keyring_service import verify_keyring_backend
from network_client import close_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────────
    logger.info("Startup: verifying keyring backend.")
    verify_keyring_backend()            # aborts if insecure

    logger.info("Startup: initialising database.")
    init_db()

    yield

    # ── Shutdown ─────────────────────────────────────────────────────────────
    logger.info("Shutdown: closing HTTP client.")
    await close_client()


app = FastAPI(
    title="Chatbot Interface API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_methods=["GET", "POST", "DELETE", "PATCH"],
    allow_headers=["Content-Type"],
    allow_credentials=False,
)

# ── Routers (uncommented phase by phase) ─────────────────────────────────────
#
# Phase 2 — Settings (active)
from routers import settings as settings_router
app.include_router(settings_router.router, prefix="/api/v1")
#
# Phase 3 — Characters (active)
from routers import characters as characters_router
app.include_router(characters_router.router, prefix="/api/v1")
#
# Phase 5B — Completions (active)
from routers import completions as completions_router
app.include_router(completions_router.router, prefix="/api/v1")
#
# Phase 4 — Chats (active)
from routers import chats as chats_router
app.include_router(chats_router.router, prefix="/api/v1")
#
# Phase 5A — Models (active)
from routers import models_router
app.include_router(models_router.router, prefix="/api/v1")
#
# Part C — Personas (active)
from routers import personas as personas_router
app.include_router(personas_router.router, prefix="/api/v1")


# ── Phase 1 liveness probe ────────────────────────────────────────────────────

@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict:
    """Simple liveness probe. Returns 200 if the server is up."""
    return {"ok": True}
