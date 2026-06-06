"""config.py — All app-wide constants. No logic, no side-effects."""

import os

# ── Network ───────────────────────────────────────────────────────────────────
BACKEND_HOST: str = "127.0.0.1"
BACKEND_PORT: int = 8787
FRONTEND_ORIGIN: str = "http://127.0.0.1:5173"

# ── Database ──────────────────────────────────────────────────────────────────
DB_PATH: str = "app.db"

# ── Keyring ───────────────────────────────────────────────────────────────────
# One service name; individual secrets identified by their key string.
KEYRING_SERVICE: str = "chatbot_interface"
KEYRING_API_KEY: str = "openrouter_api_key"
KEYRING_PROXY_URL: str = "proxy_url"

# ── OpenRouter ────────────────────────────────────────────────────────────────
# Development/testing override only. Do not set this in production.
OPENROUTER_BASE_URL: str = os.environ.get(
    "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
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

# ── Context budget ────────────────────────────────────────────────────────────
CONTEXT_SAFETY_MARGIN: int = 256  # tokens reserved as safety buffer

