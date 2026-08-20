"""config.py - All app-wide constants. No side-effects (DATA_DIR is computed,
not created; the vault/upload code makes it on first use)."""

import os
import sys
from pathlib import Path

# ── Network ───────────────────────────────────────────────────────────────────
BACKEND_HOST: str = "127.0.0.1"
BACKEND_PORT: int = 8787
FRONTEND_ORIGIN: str = "http://127.0.0.1:5173"

# DEV ONLY, and now enforced as such. In development the SPA really is a
# separate origin (5173 -> 8787) and has to be trusted. A packaged build
# serves its own SPA same-origin and never needs it - but this was an
# unconditional constant, so the shipped exe granted http://127.0.0.1:5173
# full CORS read access AND passed it through the CSRF shield. Any local page
# on 5173 - the user's own project, a cloned repo, a hostile npm dev
# dependency - could read /chats, /messages, /characters and /personas while
# the vault was open, and POST /settings/proxy to route traffic through a
# host of its choosing. Empty in a frozen build: the request's own origin is
# still allowed, which is all the packaged app ever uses.
FRONTEND_ORIGINS: tuple[str, ...] = (
    () if getattr(sys, "frozen", False) else (FRONTEND_ORIGIN,)
)

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
_DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_BASE_URL: str = os.environ.get(
    "OPENROUTER_BASE_URL", _DEFAULT_OPENROUTER_BASE_URL
)
#: The ONE explicit opt-in that lets a non-default base URL reach the egress
#: allowlist. Nothing else does, and OPENROUTER_BASE_URL on its own does not.
#:
#: Until 2026-08-20 the allowlist was DERIVED from OPENROUTER_BASE_URL, which
#: meant the gate took its guest list from the guest. One environment variable,
#: writable by any program running as the user with no elevation, moved both
#: the destination and the permission to reach it - and the Authorization
#: header went along. The single defence was a log line in a file nobody opens.
#:
#: Two variables instead of one is not a wall against a targeted attacker who
#: can already write HKCU; say that plainly rather than oversell it. What it
#: does buy is that the SILENT path is gone: a poisoned base URL alone now
#: fails loudly at the chokepoint instead of quietly succeeding, and the second
#: name says out loud what turning it on costs.
BASE_URL_OVERRIDE_ALLOWED: bool = (
    os.environ.get("ELYSIUM_ALLOW_BASE_URL_OVERRIDE", "") == "1"
)

#: True when the sole permitted network destination has been replaced. Says
#: nothing about whether that replacement is HONOURED - see the flag above.
OPENROUTER_BASE_URL_OVERRIDDEN: bool = (
    OPENROUTER_BASE_URL != _DEFAULT_OPENROUTER_BASE_URL
)


#: Set once the override has been reported, so the two entry points (run_app
#: for the frozen build, main for dev) can each call it without the packaged
#: app logging the same warning twice.
_BASE_URL_WARNED = False


def warn_if_base_url_overridden() -> None:
    """Report a redirected base URL, ONCE LOGGING EXISTS.

    Loud by design: a poisoned environment would otherwise silently redirect
    the API key - `Authorization: Bearer ...` and all - to an arbitrary host.

    It used to fire at import time, which made its visibility depend on who
    imported config first. run_app.py imports config to find DATA_DIR BEFORE it
    installs the file handler, so in the shipped `console=False` build the one
    guard against a hijacked destination went to logging.lastResort - a stderr
    no windowed exe can show - and never reached elysium.log. The fact is still
    computed at import; only the reporting is now the caller's, right after
    logging is configured.
    """
    global _BASE_URL_WARNED
    if not OPENROUTER_BASE_URL_OVERRIDDEN:  # pragma: no cover - the normal path
        return
    if _BASE_URL_WARNED:
        return
    _BASE_URL_WARNED = True
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "OPENROUTER_BASE_URL overridden to %s. Requests are REFUSED at the "
        "egress chokepoint unless ELYSIUM_ALLOW_BASE_URL_OVERRIDE=1 is also "
        "set; with it, API requests including the Authorization header go to "
        "this host.", OPENROUTER_BASE_URL,
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

# A wall-clock ceiling on one streamed completion, because STREAM_READ_TIMEOUT
# above is a PER-READ idle timeout and OpenRouter sends ": OPENROUTER
# PROCESSING" keepalive comments while a request sits in a provider queue. The
# loop skips those comments - correctly - but every one of them restarted the
# 90 s clock, so a request that never produced a single token never ended:
# no ceiling in the router, none in the frontend, and the generator kept the
# HTTP response and its worker slot open indefinitely. P4 bans exactly this
# shape.
#
# Two budgets, because they catch different stalls. The first is the one that
# matters: a provider that has not emitted a token in this long is not
# thinking, it is gone.
STREAM_FIRST_TOKEN_TIMEOUT: float = 120.0
STREAM_TOTAL_TIMEOUT: float = 900.0

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

# Multipart framing - boundary lines, part headers, trailing CRLFs - rides on
# top of the raw file bytes, so the BODY limit has to sit a little above the
# FILE cap. 64 KiB is many times what real framing costs and still rejects the
# absurd rather than the borderline.
#
# These two numbers, and uploads.py's spool ceiling, were three independent
# constants: the spool was MAX_UPLOAD_BYTES + 1 and the body shield was
# MAX_UPLOAD_BYTES + 1 MiB, leaving a ~1 MiB band where the middleware let the
# body through, Starlette rolled the file part out of RAM, and the ENTIRE
# image was written to %TEMP% in the clear before the handler returned 400 -
# the exact outcome attachments_service's "No plaintext image ever touches the
# filesystem" promises cannot happen. Deriving all three from one place is
# what closes the band: spool > body limit means nothing that survives the
# shield can ever spool.
UPLOAD_MULTIPART_OVERHEAD: int = 64 * 1024
UPLOAD_BODY_LIMIT: int = MAX_UPLOAD_BYTES + UPLOAD_MULTIPART_OVERHEAD
UPLOAD_SPOOL_LIMIT: int = UPLOAD_BODY_LIMIT + 1
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

# ── Windows hardening ─────────────────────────────────────────────────────────
# The accessibility-tree switch, and the only protection switch in this app
# that is ON when nobody asked for it.
#
# ELYSIUM_SCREEN_PRIVACY defaults OFF because its cost is visible and lands on
# the user's own screenshots. This one is the other way round. An audit built
# an unprivileged probe - no token, no HTTP, no elevation, just the same user -
# and read the whole conversation out of the WebView2 accessibility tree:
# chat title, character name, message bodies, verbatim. Screen-capture
# exclusion does not touch that path, because it excludes PIXELS and the
# accessibility tree is text.
#
# So the owner chose the stronger default knowingly. The cost is real and is
# not hidden: while this is on, a screen reader cannot read the app either.
# Anyone who needs one turns it off, WITHOUT editing code, from a command
# prompt, and this is the whole way out:
#
#     setx ELYSIUM_ACCESSIBILITY_PRIVACY 0
#
# then start Elysium again. An environment variable rather than a checkbox in
# the app, and that is the requirement rather than the easy option: a blind
# user cannot navigate to a setting inside an app their screen reader cannot
# read, and the switch has to be reachable before the vault is unlocked, which
# rules out anything stored inside it.
#
# It takes effect at STARTUP ONLY. Changing it while Elysium is running does
# nothing at all - not on the next unlock, not on the next chat. That is not a
# missing feature: the switch is a command-line argument to the WebView2
# browser process, read once when that process is created, and WebView2
# exposes no setting to change it afterwards. Anybody expecting this to behave
# like the screen-privacy switch, which the vault stores and applies on every
# lock transition, will be wrong; that one can follow the lock precisely
# because it is a Win32 call on a window that already exists.
ACCESSIBILITY_PRIVACY_ENV: str = "ELYSIUM_ACCESSIBILITY_PRIVACY"

# Exactly "0" and nothing else. A privacy control must not be turned off by a
# typo: "false", "no", "off" and an empty value all leave it ON, and the launch
# log says which state took effect so a mistyped value is visible rather than
# silently obeyed.
ACCESSIBILITY_PRIVACY_OFF: str = "0"

# ── Voice / TTS (V0) ──────────────────────────────────────────────────────────
# Elysium ships the TTS INFRASTRUCTURE only - no model weights are bundled or
# committed. The user drops a model directory into TTS_MODELS_DIR and it is
# fingerprinted from its own files. Engines run in their OWN interpreters
# (see TTS_RUNTIMES_PATH): the three supported engines have mutually
# incompatible dependency sets, so the exe never imports torch itself.
# Constants only - nothing here creates a directory.
# NOTE the directory name: DATA_DIR is the SOURCE dir in dev, so a data folder
# called "tts" would collide with the backend/tts/ python package that holds the
# engine adapters. Data lives under "voice/", code under "tts/" - never mixed.
TTS_DIR: Path = DATA_DIR / "voice"
TTS_MODELS_DIR: str = str(TTS_DIR / "models")    # the drop folder (one dir per model)
TTS_REFS_DIR: str = str(TTS_DIR / "refs")        # reference clips + transcripts
TTS_CACHE_DIR: str = str(TTS_DIR / "cache")      # conditioning caches, PCM handoff
TTS_RUNTIMES_PATH: str = str(TTS_DIR / "runtimes.json")  # engine_id -> interpreter

# Scan bounds. HF snapshot layouts nest as models--X--Y/snapshots/<rev>/, hence
# depth 3; the dir cap stops a mis-pointed root (e.g. C:\) from hanging a scan.
TTS_SCAN_MAX_DEPTH: int = 3
TTS_SCAN_MAX_DIRS: int = 2000

# VRAM policy. A pre-load check refuses to load when the estimate plus this
# headroom exceeds free VRAM - the user games on this machine, and a model that
# fills the card makes the desktop crawl instead of failing cleanly.
TTS_VRAM_HEADROOM_MB: int = 1024
TTS_FOREIGN_VRAM_WARN_MB: int = 2048   # warn when other apps already hold this much

# Lifecycle. The first load of a compiling engine is genuinely slow (kernel
# compilation); the timeout must not mistake that for a hang.
#
# There is no idle unload any more. Ten minutes of silence was a GUESS that the
# user had gone away, and it was wrong in both directions: it threw away a model
# that takes 60-99 s to rebuild while someone was still reading, and it held the
# card for ten minutes after they really had left. The vault lock replaces it -
# an explicit act, not an inference. See `VoiceHost.on_vault_locked`.
TTS_LOAD_TIMEOUT_S: int = 180
# Spawn -> "ready". Short: this is only interpreter startup, before any weights.
TTS_HANDSHAKE_TIMEOUT_S: int = 60
#: How long a generated wav stays on disk before the next synthesis clears it.
#: The cache is the user's conversation in AUDIBLE form sitting in the clear
#: next to a database that went to the trouble of being encrypted, and until
#: this existed its only bound was "until the vault locks" - unbounded within a
#: session. Long enough that replaying a reply still on screen works; short
#: enough that a day of chatting is not a transcript anyone can play back.
TTS_CACHE_MAX_AGE_S: int = 30 * 60

TTS_SYNTH_TIMEOUT_S: int = 180
# The host's own heartbeat: notices dead workers and reclaims idle VRAM even
# when no UI is polling (a minimised window polls nothing).
TTS_HEALTH_POLL_S: int = 30
# Provisioning: a 2.6 GB CUDA wheel behind an antivirus scanner is not quick.
TTS_INSTALL_TIMEOUT_S: int = 3600
TTS_INSTALL_MIN_FREE_GB: int = 14
TTS_BIN_DIR: str = str(TTS_DIR / "bin")
TTS_ENVS_DIR: str = str(TTS_DIR / "envs")
TTS_PY_DIR: str = str(TTS_DIR / "python")
TTS_UV_CACHE_DIR: str = str(TTS_DIR / "uv-cache")
TTS_REF_MIN_S: float = 3.0
TTS_REF_MAX_S: float = 30.0
TTS_REF_MAX_BYTES: int = 30 * 1024 * 1024


# ---------------------------------------------------------------------------
# Selected model (v1.2 privacy fix)
# ---------------------------------------------------------------------------
#: Which model is currently chosen in the UI. An OpenRouter model id such as
#: "anthropic/claude-3.5-sonnet" is a NAME a person reads on screen - exactly
#: the shape the owner's own rule bans from ever sitting outside the vault -
#: so it lives here, next to the API key and the stop sequences, and never in
#: the browser's localStorage. It used to be one of three keys in uiStore's
#: `elysium-ui-state` blob; the other two (selectedChatId, selectedCharacterId)
#: are bare numbers and stay in localStorage, which the rule permits. See
#: routers/settings.py's POST /settings/model-selection and uiStore.ts's
#: version-3 `migrate`, which cleans the plaintext copy out of any install
#: that already has one.
SETTING_SELECTED_MODEL_ID = "selected_model_id"
#: OpenRouter model ids are short slugs ("vendor/name"); this is a generous
#: ceiling against a malformed or hostile value, not a real-world size. This
#: setting is UI state, not a security boundary, so it clamps rather than
#: rejects - the same choice save_stop_sequences and the proxy alias make.
SELECTED_MODEL_ID_MAX_CHARS = 200


# ---------------------------------------------------------------------------
# Notebook extraction (FAZ 4/5)
# ---------------------------------------------------------------------------
#: Which model proposes notes. NO DEFAULT: unset means extraction never runs.
#: A background job spending somebody's own API credits on a model they never
#: chose is not a convenience, and the list they choose from is filtered to
#: endpoints that both honour a strict JSON schema and carry the same
#: zero-retention policy the conversation itself does.
SETTING_NOTEBOOK_MODEL = "notebook_extract_model"

#: Which language the extraction INSTRUCTIONS are written in ("en" or "tr").
#: English by default. The assumption that English instructions are safer is
#: unmeasured - the literature is model-dependent and its direction is not
#: predictable - so both exist and the dry run is what settles it.
SETTING_NOTEBOOK_PROMPT_LANG = "notebook_prompt_language"

#: Automatic acceptance. ON by default, and the reason is measured: a review
#: queue nobody empties makes the feature useless, and the comparable domain
#: shows a 90% dismissal rate for alerts that INTERRUPT, which this does not.
#: What it costs is stated where it is used - in automatic mode human review is
#: not a defence, and the code filter, the weak slot, the ceiling and the
#: source stamp become load-bearing.
SETTING_NOTEBOOK_AUTO_ACCEPT = "notebook_auto_accept"

#: Turns between extractions.
NOTEBOOK_EXTRACT_EVERY_TURNS = int(os.environ.get(
    "ELYSIUM_NOTEBOOK_EVERY_TURNS", "20"))

#: The one control in this feature that is a control.
#:
#: Everything else about limits is a paragraph in a prompt, and a paragraph in
#: a prompt is a request. This is matched in CODE, before the provider is
#: called, and when it matches nothing is sent at all - not the message, not
#: the notebook, not the limits. The scene stops because the request never
#: leaves, which is the only version of "stop" that does not depend on a model
#: agreeing to it.
SETTING_SAFEWORD = "notebook_safeword"

#: A hard daily ceiling, enforced as a BLOCK before the call rather than an
#: alert after it. The largest documented runaway in this space was not a loop:
#: it was a context that grew every call while a budget alarm dutifully fired.
NOTEBOOK_DAILY_CALL_CAP = int(os.environ.get(
    "ELYSIUM_NOTEBOOK_DAILY_CALLS", "60"))

