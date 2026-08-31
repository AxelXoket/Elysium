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

import asyncio
import contextlib
import logging
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import auto_lock
import launch_token
import vault_state
import config as config_module
from config import (FRONTEND_ORIGINS, MAX_UPLOAD_BYTES,
                    TTS_REF_BODY_LIMIT, UPLOAD_BODY_LIMIT)
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

    # Clear a browser profile the LAST session left behind, before this one
    # can add to it.
    #
    # The packaged launcher already covers itself: run_app.py calls
    # clear_session_residue() at :787, BEFORE webview.start(), so a crash or a
    # kill is cleaned up by the next launch. (A first draft of this comment
    # said that call was on the way out and that nothing reached it after a
    # crash. That was wrong - :787 is the startup path, :794 is the exit one -
    # and a watchdog caught it. The fix below is still needed; its reason is
    # narrower than first written.)
    #
    # The gap is the DEV path: start_backend.bat runs uvicorn directly and
    # never imports run_app, so on that path nothing has ever cleared the
    # profile. Measured again on 2026-08-30 and it has not moved:
    # 13,180,982 bytes across 175 files in 56 folders, every one of them
    # still dated 25 July, with ten carrying first_mes and ten carrying
    # system_prompt as plain readable JSON, invisible to `git status`
    # because the folder is gitignored. The earlier note rounded this
    # to a larger figure and was never re-measured after it was written.
    #
    # Here rather than in run_app because this lifespan is the one thing both
    # entry points share, and it runs before any window exists - uvicorn
    # binds in a thread first and pywebview opens afterwards, so there is no
    # live profile to pull out from under.
    try:
        import browser_profile
        from config import DATA_DIR

        stale = browser_profile.purge(Path(DATA_DIR) / "webview")
        if stale:
            logger.info("Startup: shredded %d cached file(s) a previous "
                        "session left in the browser profile.", stale)
    except Exception:                            # noqa: BLE001
        # Same rule as every other optional subsystem here: failing to start
        # is worse than residue, and purge() is documented never to raise.
        logger.warning("Startup: could not sweep the browser profile.")

    # The idle watchdog. Cheap (one wakeup every AUTO_LOCK_TICK_S) and inert
    # until the user turns auto-lock on, but started here rather than at
    # unlock so there is exactly one of it for the process lifetime.
    watchdog = asyncio.create_task(auto_lock.watch())

    # The notebook's extractor, beside the watchdog and for the same reason:
    # exactly one for the process lifetime. It is inert until a model is
    # chosen, and it holds its own task reference - the event loop keeps only
    # a WEAK one, so a worker started and forgotten is collected at an
    # arbitrary moment and the feature simply stops with nothing in the log.
    # Guarded like every other optional subsystem in this lifespan. A raise
    # here aborts ASGI startup: uvicorn never binds, the vault can never be
    # unlocked, and a notebook that failed to import presents as "the app does
    # not start" rather than "the notebook is off".
    try:
        import notebook_worker
        notebook_worker.start()
    except Exception:
        notebook_worker = None
        logger.warning("Startup: the notebook worker did not start.",
                       exc_info=True)

    yield

    watchdog.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await watchdog

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

    # LAST. Its database writes run in a worker thread anyio will not abandon,
    # under a 15-second busy timeout, so this await can hold shutdown for that
    # long - and nothing else should wait behind it. Best effort either way:
    # in the packaged app uvicorn is a daemon thread and this never runs.
    if notebook_worker is not None:
        await notebook_worker.stop()


app = FastAPI(
    title="Elysium API",
    version="1.1.6",
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
#: Routes the FRONTEND polls on a timer. None of them may feed the idle clock:
#: a request nobody made is not activity, and one of these is enough to hold
#: the vault open forever.
#:
#: Adding a poller means adding it here, in the same commit. The alternative
#: is what happened when the notebook's status card arrived: a second timer,
#: no change here, and auto-lock quietly stopped existing.
#: vault_gate below compares this set against request.url.path - the RESOLVED
#: path a browser actually asked for, never a route template. A literal
#: "{engine_id}" string would sit in this set forever and match nothing: the
#: install-status poll is on a parameterized route, so its exemption has to be
#: every concrete path that route can resolve to, built from the same engine
#: registry the route itself validates against (routers/tts_runtime.py's
#: _known_engine uses this exact call). Adding an engine adapter therefore
#: exempts its install poll automatically - nobody has to remember this set
#: exists on that day, the same way they had to remember it for the notebook.
from tts.registry import all_adapters as _tts_engines

_IDLE_EXEMPT: frozenset[str] = frozenset({
    "/api/v1/vault/status",
    "/api/v1/notebook/worker",
    "/api/v1/tts/state",
    # Polled every 700ms while an engine install runs (frontend/src/lib/
    # query/tts.ts). routers/vault.py's own teardown already treats a running
    # install as independent of vault state - killing a multi-GB download
    # because the user locked the screen punishes the cautious behaviour this
    # app wants. The payload (tts/provision.py's _Job.to_json) is engine_id,
    # a state enum, a log of setup/download progress lines, an error code and
    # detail string, two timestamps and a running flag - install progress,
    # never a message.
    #
    # This also exempts the POST that STARTS an install, since it shares the
    # same path and the gate has no way to tell methods apart without touching
    # code outside this set. That is accepted: a single deliberate click not
    # resetting the clock is nothing like a request nobody made every 700ms -
    # the click sits among other real requests (opening the runtime panel,
    # reading the plan) that already count.
    *(f"/api/v1/tts/runtimes/{a.engine_id}/install" for a in _tts_engines()),
    # Polled every 1500ms while a voice model is loading (same file). Unlike
    # the install above, locking the vault DOES tear this down: on_vault_
    # locked() -> unload() bumps tts/host.py's _generation counter, and load()
    # checks that counter against the round trip it started with, so a lock
    # that lands mid-load aborts it cleanly rather than abandoning it. That
    # makes exempting this route SAFE rather than just convenient: a load
    # that never reaches a terminal state (the client timeout alone is 180s,
    # already longer than the shortest 1-minute auto-lock) would otherwise
    # hold the vault open for the whole stall, since 1.5s < 60s is exactly
    # the notebook poll's shape. Exempting it means a stalled load gets
    # reclaimed by auto-lock instead of being kept alive by a poll nobody at
    # the keyboard is answering. The payload (routers/tts.py get_active) is a
    # model uid, a state enum, engine_id, a VRAM number, an error code, a
    # readiness verdict of issue codes/booleans/language tags, and a boolean -
    # never message content.
    "/api/v1/tts/active",
})


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
    # Idle is measured HERE because this is the one place every API request
    # passes through. Polling routes would keep the vault open forever if
    # they counted, so every route the frontend asks for ON A TIMER - whether
    # or not anybody is at the keyboard - is exempt.
    #
    # This was one route, and adding a second poller silently disabled
    # auto-lock: the notebook's status card refreshes every 20 seconds while
    # the Notes tab is open, and the shortest configurable timeout is one
    # minute, so the idle clock could never reach it. The vault stopped
    # locking itself and nothing said so. A poll is not a person.
    if not path.startswith("/api/v1") or path in _IDLE_EXEMPT:
        return await call_next(request)

    vault_state.enter_request()
    try:
        response = await call_next(request)
    except BaseException:
        vault_state.leave_request()
        raise

    # NOT a `finally` around call_next, and this is the whole point.
    # BaseHTTPMiddleware returns from call_next the instant the endpoint sends
    # http.response.start - which StreamingResponse does BEFORE touching its
    # body iterator. So for every streamed reply the counter went back to zero
    # while the generation was still running, and the idle clock restarted at
    # the START of a forty-minute stream. Auto-lock would then clear the key
    # and close the HTTP client out from under a reply the user was reading.
    #
    # The counter has to be released when the BODY finishes, so the release
    # rides on the iterator that produces it.
    iterator = getattr(response, "body_iterator", None)
    if iterator is None:
        vault_state.leave_request()
        return response

    async def counted():
        try:
            async for chunk in iterator:
                yield chunk
        finally:
            # finally, not after the loop: a client that closes the tab
            # mid-stream cancels this generator, and a counter that only
            # decremented on the happy path would stick above zero and
            # disable auto-lock for the rest of the session.
            vault_state.leave_request()

    response.body_iterator = counted()
    return response


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

#: Every multipart route, and the body each one may declare.
#:
#: The voice-clip route was missing. It reads an audio file the user recorded
#: of their own voice, its cap is three times the image one, and it had no
#: shield at all: the body was read in full and measured afterwards.
#:
#: Longest prefix wins, so a more specific route can carry its own number.
_BODY_LIMITS: tuple[tuple[str, int], ...] = (
    ("/api/v1/uploads/", UPLOAD_BODY_LIMIT),
    ("/api/v1/tts/voices/", TTS_REF_BODY_LIMIT),
)


def _declared_limit(path: str) -> int | None:
    """The body ceiling for this path, or None if it is not a upload route."""
    best: int | None = None
    longest = -1
    for prefix, limit in _BODY_LIMITS:
        if path.startswith(prefix) and len(prefix) > longest:
            best, longest = limit, len(prefix)
    return best


@app.middleware("http")
async def reject_oversized_upload(request: Request, call_next):
    limit = (_declared_limit(request.url.path)
             if request.method == "POST" else None)
    if limit is not None:
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
        if declared > limit:
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


#: Loaded by an element rather than by fetch, so no header can ride along.
_ELEMENT_LOADED = re.compile(
    r"^/api/v1/(uploads/images/\d+|tts/audio/[^/]+)$")


@app.middleware("http")
async def launch_token_gate(request: Request, call_next):
    """Only the window this launch opened may use the API.

    Every other guard on this server assumes the attacker is a web page.
    Loopback is not a permission boundary: any program running as this user
    can reach 127.0.0.1 and read the whole conversation while the app is open,
    which is precisely when the vault is unlocked.

    Registered here, beside csrf_shield, so it runs on the same layer and
    inside CORS - a 403 from it still carries CORS headers and the frontend
    sees a catchable error rather than an opaque network failure.

    Unarmed unless run_app issued a token, so a developer running uvicorn by
    hand is unaffected. GET /healthz stays open: the launcher polls it BEFORE
    the window exists, so it cannot present a token yet, and it answers a
    fixed string with nothing of the user's in it.
    """
    path = request.url.path
    if not path.startswith("/api/v1") or request.method == "OPTIONS":
        return await call_next(request)
    if launch_token.accepts(request.headers.get(launch_token.HEADER)):
        return await call_next(request)
    # Two routes are loaded by the BROWSER ITSELF - <img src> for a stored
    # picture and the audio element for a spoken reply - and an element load
    # cannot carry a custom header. Rewriting both to fetch-into-a-blob is the
    # complete answer and is not this change.
    #
    # They are not simply exempted, and the narrowing is smaller than this
    # comment used to claim. Sec-Fetch-Site is set by the browser and cannot
    # be forged from the page, so a hostile PAGE is still refused. A local
    # program is not: curl sends the header the moment somebody types -H,
    # and the earlier wording said it was turned away. What this buys is
    # that the two element-loaded routes are unreachable from another
    # origin, not that they are unreachable from another process.
    if (request.method == "GET"
            and _ELEMENT_LOADED.match(path)
            and request.headers.get("sec-fetch-site") == "same-origin"):
        return await call_next(request)
    return JSONResponse({"detail": "launch_token_invalid"}, status_code=403)


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


#: The one policy, built once. Four headers, not the usual dozen: this origin is
#: http://127.0.0.1:<random> inside a WebView2 window, so most public-web advice
#: closes nothing here. What is LEFT OUT is deliberate:
#:   Strict-Transport-Security - ignored over http, and pinning localhost to
#:                               https would sabotage every other dev server.
#:   Referrer-Policy           - the Fetch default is already
#:                               strict-origin-when-cross-origin and this
#:                               renderer makes zero cross-origin requests.
#:   Permissions-Policy        - closes nothing without an untrusted-script
#:                               path; no camera, mic or geolocation is used.
#:   COOP / COEP               - no cross-origin opener, no SharedArrayBuffer.
#:   X-XSS-Protection          - the XSS Auditor was removed in Chrome 78.
#:   upgrade-insecure-requests - NEVER add this. Nothing exempts localhost, so
#:                               it would rewrite every same-origin fetch to
#:                               https://127.0.0.1:<port>, where nothing is
#:                               listening. An instant brick.
_CSP = (
    # The point of a CSP here is NOT XSS mitigation - there is no HTML sink in
    # this SPA (no innerHTML, no dangerouslySetInnerHTML, no markdown-to-HTML).
    # It is EXFILTRATION CONTAINMENT. The same origin serves the whole
    # unauthenticated vault API, so if anything ever did execute, this is what
    # denies it a way out: no fetch to another host, no <img src> beacon, no CSS
    # url() callback.
    "default-src 'self'; "
    "script-src 'self'; "
    # 'unsafe-inline' is unavoidable for styles, twice over: the SPA uses inline
    # style attributes throughout, and pywebview injects its own <style> element
    # on every navigation (its text_select default is False). The tighter
    # style-src-attr split would silently break the second one. The mitigation
    # for accepting it is img-src below - that removes the CSS exfil channel,
    # which is the only thing inline style would otherwise buy an attacker.
    "style-src 'self' 'unsafe-inline'; "
    # blob: is required, and by two separate features: attachment previews
    # before upload, and the chat wallpaper's CSS background-image.
    "img-src 'self' blob:; "
    "object-src 'none'; "
    "frame-src 'none'; "
    "base-uri 'none'; "
    # Every <form> in the app calls preventDefault(); none navigates.
    "form-action 'none'; "
    # The reason this is a HEADER and not a <meta http-equiv>: frame-ancestors
    # is one of the directives a meta policy must ignore. StaticFiles could not
    # template a per-request nonce anyway.
    "frame-ancestors 'none'"
)

#: Only in the packaged build. This closes a real gap: csrf_shield exempts GET
#: by design and TrustedHost allows 127.0.0.1, so a remote page that guesses the
#: port can <img src> a private attachment or a synthesised audio file. It
#: cannot read the pixels, but it learns the file exists and its dimensions.
#: CORP refuses the no-cors load outright.
#:
#: Frozen-only because in dev the SPA is on :5173 and the API on :8787, and
#: measurement (not spec reading) shows Chromium treats same-site exactly like
#: same-origin across ports on an IP host - so any value strict enough to help
#: would break dev attachment thumbnails and the voice preview button, the
#: latter silently. Same convention as config.FRONTEND_ORIGINS, which is also
#: keyed off `frozen`.
_CORP = "same-origin" if getattr(sys, "frozen", False) else None


# Registered after no_store_api, so THIS is now the outermost layer. That is
# required, not cosmetic: vault_gate answers 423 and csrf_shield answers 403
# without calling downstream, and those responses need the headers too.
@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Baseline response headers for every route, including short-circuits."""
    response = await call_next(request)
    h = response.headers
    # setdefault, not assignment: a route that has deliberately chosen its own
    # value keeps it, exactly as no_store_api does with Cache-Control.
    h.setdefault("Content-Security-Policy", _CSP)
    # The image and audio routes serve bytes whose Content-Type comes from a DB
    # column. nosniff stops a browser from second-guessing that column.
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    if _CORP:
        h.setdefault("Cross-Origin-Resource-Policy", _CORP)
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
# FAZ 1 - Notebook + boundaries (active). Sits behind the vault gate like every
# other data route: its rows are derived from chat content and are therefore
# chat content.
from routers import notebook as notebook_router
app.include_router(notebook_router.router, prefix="/api/v1")
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
