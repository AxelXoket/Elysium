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
import config as config_module
from config import FRONTEND_ORIGINS, MAX_UPLOAD_BYTES, UPLOAD_BODY_LIMIT
from network_client import close_client
from vault_state import VaultLockedError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Emitted HERE, not at config import: whoever imports config first decides
# whether a handler exists yet, and in the frozen build it does not.
config_module.warn_if_base_url_overridden()


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
    # The voice worker first: it holds gigabytes of VRAM, and this path is the
    # graceful one. It is NOT the only one - in the packaged app uvicorn runs
    # in a daemon thread and this never executes, so run_app.py registers the
    # same teardown on the window-closed event and on atexit, with the job
    # object underneath as the guarantee that survives a hard kill.
    logger.info("Shutdown: stopping the voice worker.")
    try:
        from tts.worker_client import hard_close
        hard_close(grace=1.0)
    except Exception:
        # A voice teardown failure must not stop the HTTP client from closing
        # cleanly - the two have nothing to do with each other.
        logger.warning("Shutdown: voice teardown failed.", exc_info=True)
    logger.info("Shutdown: closing HTTP client.")
    await close_client()


app = FastAPI(
    title="Elysium API",
    version="1.1.0",
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


# ── Oversized body shield ─────────────────────────────────────────────────────
# FastAPI resolves an `UploadFile` parameter by draining the WHOLE multipart
# body first, so a handler-side size check runs only AFTER the write has already
# happened: `curl -F file=@2GB.bin .../uploads/images` streamed 2 GB into the
# temp directory and only then got 400 attachment_too_large - a single request
# could fill the disk. Content-Length is what a browser and curl both send, and
# refusing here means the body is never read at all.
#
# The bound is generous: multipart framing (boundaries, part headers) adds a
# little to the raw file bytes, so this rejects the absurd, not the borderline.
# The exact per-file cap stays where it belongs, in the handler.
_UPLOAD_BODY_LIMIT = UPLOAD_BODY_LIMIT


@app.middleware("http")
async def reject_oversized_upload(request: Request, call_next):
    if request.method == "POST" and request.url.path.startswith("/api/v1/uploads/"):
        raw = request.headers.get("content-length")
        if raw is None:
            # A chunked POST declares no length, so this shield simply did not
            # run for it and the body was read anyway - the one shape that
            # skipped the check the comment above promises. No browser
            # produces it for a form upload; a local process can, and a local
            # process is inside the threat model this shield exists for.
            return JSONResponse(
                {"detail": "attachment_invalid"}, status_code=411,
            )
        try:
            declared = int(raw)
        except ValueError:
            return JSONResponse({"detail": "attachment_invalid"}, status_code=400)
        if declared > _UPLOAD_BODY_LIMIT:
            return JSONResponse(
                {"detail": "attachment_too_large"}, status_code=400,
            )
    return await call_next(request)


# ── Cross-origin write shield (CSRF) ──────────────────────────────────────────
# CORS blocks a hostile page from READING our responses, not from SENDING a
# state-changing request: `fetch(".../chats/1/clear", {method:"POST",
# mode:"no-cors"})` from any site still reaches the handler while the vault is
# unlocked. This middleware rejects cross-origin mutations. Registered AFTER
# vault_gate and BEFORE CORS so it runs just outside the gate and its 403 still
# carries CORS headers (the frontend can catch it instead of an opaque failure).
#
# The allow-set is {FRONTEND_ORIGIN, the request's OWN origin}: dev is genuinely
# cross-origin (5173 -> 8787) and matches via FRONTEND_ORIGIN; the packaged app
# is served same-origin on a random port and matches via its own origin. Origin
# takes precedence over Sec-Fetch-Site precisely so a legitimate cross-origin
# dev POST (which carries Sec-Fetch-Site: same-site) is NOT rejected.
#
# Residual accepted gap (unchanged from v1.0): a LOCAL non-browser process can
# omit Origin/Sec-Fetch-Site and is allowed - the same trust boundary as the
# in-RAM vault key. A process-scoped token would only close it via an
# out-of-band pywebview injection channel that dev cannot share; deferred to a
# later version with that reasoning rather than shipping a dev/prod split.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _request_own_origin(request: Request) -> str:
    """scheme://host[:port] of the API itself, for same-origin comparison."""
    return f"{request.url.scheme}://{request.url.netloc}"


@app.middleware("http")
async def csrf_shield(request: Request, call_next):
    if request.method in _SAFE_METHODS or not request.url.path.startswith("/api/v1"):
        return await call_next(request)

    origin = request.headers.get("origin")
    if origin is not None:
        allowed = {*FRONTEND_ORIGINS, _request_own_origin(request)}
        if origin not in allowed:
            return JSONResponse({"detail": "cross_origin_denied"}, status_code=403)
    else:
        # No Origin header: fall back to the Fetch Metadata signal. A browser
        # cross-site/same-site request without Origin is a forgery attempt;
        # same-origin, none (user-initiated), and absent (non-browser tooling
        # like the TestClient) are allowed.
        sfs = request.headers.get("sec-fetch-site")
        if sfs is not None and sfs not in ("same-origin", "none"):
            return JSONResponse({"detail": "cross_origin_denied"}, status_code=403)

    return await call_next(request)


# Backstop for the gate's check-then-act window: if the vault locks AFTER the
# middleware check but BEFORE a handler's get_db(), VaultLockedError would
# otherwise become a 500 + traceback. Map it to the same 423 the gate returns.
@app.exception_handler(VaultLockedError)
async def _vault_locked_handler(request: Request, exc: VaultLockedError):
    return JSONResponse({"detail": "vault_locked"}, status_code=423)


# A voice error that escapes a /tts handler still carries a code the frontend
# maps by exact string. Without this net it would surface as Starlette's plain
# "Internal Server Error", which the client renders as the generic toast - the
# outcome the whole error vocabulary exists to prevent. Scoped to TtsError
# only: everything else keeps its existing behaviour.
from tts.errors import ALL_CODES as _TTS_CODES, TtsError as _TtsError


@app.exception_handler(_TtsError)
async def _tts_error_net(request: Request, exc: _TtsError):
    code = exc.code if exc.code in _TTS_CODES else "tts_worker_failed"
    return JSONResponse({"detail": code}, status_code=500)


app.add_middleware(
    CORSMiddleware,
    allow_origins=list(FRONTEND_ORIGINS),
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
# Registered LAST so it is the OUTERMOST layer. Starlette wraps
# later-registered middleware around earlier ones, and the vault gate
# short-circuits a locked request with 423 without calling downstream - so
# an inner layer would never see those responses at all.
@app.middleware("http")
async def no_store_api(request: Request, call_next):
    """Keep the conversation out of the browser's on-disk cache.

    The packaged app runs WebView2 with a PERSISTENT profile (run_app.py sets
    `private_mode=False` so font size and the last-open chat survive a restart),
    which leaves Chromium free to write cacheable responses to disk - and the
    JSON data routes carry the whole conversation, characters and personas in
    plaintext, outside the encrypted vault.

    Everything else leaving this server had already been given `no-store` for
    exactly this reason (uploads, the synthesised audio, the SSE headers). The
    ordinary data routes were the gap, and they are the largest volume of the
    most sensitive content in the app.

    Scoped to /api/: the SPA bundle is hashed static output and SHOULD be
    cached - making it uncacheable would slow every launch to protect nothing.
    A response that already chose a policy (SSE's `no-cache`) keeps its own.
    """
    response = await call_next(request)
    if (request.url.path.startswith("/api/")
            and "cache-control" not in response.headers):
        response.headers["Cache-Control"] = "no-store"
    return response




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
#
# Phase M - Voice / TTS model discovery + per-model settings (active).
# Host half only: no torch, no worker, no GPU access from this router.
from routers import tts as tts_router
app.include_router(tts_router.router, prefix="/api/v1")

#
# Phase V3 - Voice that actually runs: the worker lifecycle, app-owned engine
# setup, and playback. Shares the /tts prefix; routers/tts.py stays the pure
# host half. This process still never imports torch - it only spawns.
from routers import tts_runtime as tts_runtime_router
app.include_router(tts_runtime_router.router, prefix="/api/v1")


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
