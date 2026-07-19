"""config.py - All app-wide constants. No side-effects (DATA_DIR is computed,
not created; the vault/upload code makes it on first use)."""

import os
import sys
from pathlib import Path

# ── Network ───────────────────────────────────────────────────────────────────
BACKEND_HOST: str = "127.0.0.1"
BACKEND_PORT: int = 8787
FRONTEND_ORIGIN: str = "http://127.0.0.1:5173"

# ── Data directory ────────────────────────────────────────────────────────────
# Where the encrypted DB, the vault identity files (salt/verifier), and the
# uploads live. A packaged (frozen) build must NOT write beside its possibly
# read-only install location, so it uses a per-user data dir. Dev keeps
# everything beside the code so the in-progress vault is never orphaned by
# this switch. ELYSIUM_DATA_DIR overrides both (tests/CI).
def _resolve_data_dir() -> Path:
    override = os.environ.get("ELYSIUM_DATA_DIR")
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA") or str(
            Path.home() / "AppData" / "Local"
        )
        return Path(base) / "Elysium"
    return Path(__file__).resolve().parent


DATA_DIR: Path = _resolve_data_dir()

# ── Database ──────────────────────────────────────────────────────────────────
DB_PATH: str = str(DATA_DIR / "app.db")

# ── Secrets (E5: sealed in the encrypted vault DB) ───────────────────────────
# Row names in vault_secrets. They deliberately EQUAL the legacy OS-keyring
# usernames so the one-time keyring->vault migration maps 1:1.
SECRET_API_KEY: str = "openrouter_api_key"
SECRET_PROXY_URL: str = "proxy_url"

# Legacy OS-keyring service name - read ONLY by the one-time migration
# (keyring_service.read_legacy/delete_legacy). This is the repo's historical
# name; existing users' Credential Manager entries live under it.
KEYRING_SERVICE: str = "chatbot_interface"

# ── OpenRouter ────────────────────────────────────────────────────────────────
# Development/testing override only. Do not set this in production.
OPENROUTER_BASE_URL: str = os.environ.get(
    "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
)
if OPENROUTER_BASE_URL != "https://openrouter.ai/api/v1":  # pragma: no cover
    # Dev/test override only. Loud by design: a poisoned environment would
    # otherwise silently redirect the keyring API key to an arbitrary host.
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "OPENROUTER_BASE_URL overridden to %s - API requests (including the "
        "Authorization header) go to this host.", OPENROUTER_BASE_URL,
    )

# Injected under the "provider" key in every chat completion request body.
# zdr, data_collection, and allow_fallbacks are locked and cannot be
# overridden by the frontend. Only require_parameters may be overridden.
PROVIDER_POLICY: dict = {
    "zdr": True,
    "data_collection": "deny",
    "allow_fallbacks": False,
    "require_parameters": True,
}

# ── Cache TTLs (seconds) ──────────────────────────────────────────────────────
PROXY_HEALTH_TTL: int = 30
MODEL_LIST_TTL: int = 300

# ── Per-operation HTTP timeouts (seconds) ─────────────────────────────────────
# Each caller specifies its own timeout; the shared client has no global timeout.
HEALTH_PROBE_TIMEOUT: float = 5.0
MODELS_FETCH_TIMEOUT: float = 15.0
COMPLETION_TIMEOUT: float = 120.0

# Streaming completions: connect fast, then allow up to STREAM_READ_TIMEOUT of
# silence between chunks (OpenRouter sends ": OPENROUTER PROCESSING" keepalive
# comments, so a healthy stream is never silent for long).
STREAM_CONNECT_TIMEOUT: float = 15.0
STREAM_READ_TIMEOUT: float = 90.0

# ── Context budget ────────────────────────────────────────────────────────────
CONTEXT_SAFETY_MARGIN: int = 256  # tokens reserved as safety buffer

# Character-per-token estimate used for history trimming. Deliberately
# conservative (3 instead of the English-typical 4): Turkish and other
# agglutinative languages tokenize at ~2-3 chars/token, and overestimating
# the budget risks provider-side context overflows.
CHARS_PER_TOKEN_ESTIMATE: int = 3

# ── Image attachments ─────────────────────────────────────────────────────────
# Image bytes live INSIDE the encrypted DB (attachment_blobs, E6).
# UPLOADS_DIR remains ONLY as the legacy plaintext location the one-time
# unlock migration sweeps into blobs (and where a backup-restored user's old
# files would reappear); nothing writes new files there.
UPLOADS_DIR: str = str(DATA_DIR / "uploads")
MAX_UPLOAD_BYTES: int = 10 * 1024 * 1024   # 10 MiB per image
MAX_ATTACHMENTS_PER_MESSAGE: int = 4
IMAGE_MAX_DIMENSION: int = 2048            # longest side; larger gets downscaled
# RAM ceiling for provider-payload assembly: blobs are prefetched newest-first
# up to this many total bytes; images beyond the cap are dropped from the
# payload (with a warning), the request still proceeds. Processed images are
# typically well under 2 MB, so the cap only bites pathological histories.
IMAGE_PAYLOAD_MAX_TOTAL_BYTES: int = 64 * 1024 * 1024
ALLOWED_IMAGE_MIMES: dict[str, str] = {    # mime -> file extension
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}
# Flat per-image token estimate for context budgeting. Providers differ
# (OpenAI-style tiling, Anthropic w*h/750, ...); 1100 is a conservative
# middle ground for a downscaled photo. Keep in sync with the frontend
# estimator (lib/context).
IMAGE_TOKEN_ESTIMATE: int = 1100

