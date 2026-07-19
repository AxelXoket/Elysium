"""
Phase 5A verification script (OpenRouter Models Router).
Run from backend/ with the virtual environment active:
    .venv/Scripts/python verify_phase5a.py

Safety guarantees:
  - Uses a local fake OpenRouter server (127.0.0.1) - no real internet.
  - Backs up and restores keyring API key and proxy URL.
  - Backs up and restores DB settings rows touched by tests.
  - If app.db did not exist, it is removed after the run.
  - API key, proxy URL, and raw response bodies are never printed.
"""

import sys
import os
import json
import time
import sqlite3
import subprocess
import threading
import urllib.request
import urllib.error
import http.server

# ── sys.path ──────────────────────────────────────────────────────────────────
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)

# Force UTF-8 output (cp1254 terminal safety).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── Helpers ───────────────────────────────────────────────────────────────────
PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results: list[tuple[str, bool]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    tag = PASS if ok else FAIL
    msg = f"  [{tag}] {label}"
    if detail:
        msg += f"  ->  {detail}"
    print(msg)
    results.append((label, ok))


def section(title: str) -> None:
    print(f"\n{'-'*62}\n  {title}\n{'-'*62}")


BASE = "http://127.0.0.1:8787"
_all_responses: list[str] = []


def http_get(path: str) -> tuple[int, dict | list | None]:
    url = f"{BASE}{path}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            body = resp.read().decode()
            _all_responses.append(body)
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        _all_responses.append(body)
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"raw": body}


def http_post(path: str, data: dict) -> tuple[int, dict | None]:
    url = f"{BASE}{path}"
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode()
            _all_responses.append(raw)
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        _all_responses.append(raw)
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


def http_delete(path: str) -> tuple[int, dict | None]:
    url = f"{BASE}{path}"
    req = urllib.request.Request(url, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode()
            _all_responses.append(raw)
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        _all_responses.append(raw)
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


# ── Fake OpenRouter server ────────────────────────────────────────────────────
FAKE_PORT = 19876

# Shared state (main thread sets, handler thread reads)
_fake_mode = "normal"
_calls_user = 0
_calls_public = 0
_last_auth_header_user: str | None = None
_last_auth_header_public: str | None = None

FAKE_MODEL_FULL = {
    "id": "openai/gpt-4",
    "name": "GPT-4",
    "description": "A powerful language model",
    "context_length": 128000,
    "architecture": {
        "input_modalities": ["text", "image"],
        "output_modalities": ["text"],
    },
    "pricing": {"prompt": "0.00003", "completion": "0.00006"},
    "top_provider": {
        "max_completion_tokens": 4096,
        "context_length": 128000,
    },
    "supported_parameters": ["temperature", "top_p", "max_tokens"],
    "created": 1700000000,
    "canonical_slug": "openai/gpt-4",
}

FAKE_MODEL_MINIMAL = {
    "id": "test/minimal-model",
    # All other fields intentionally missing - tests normalization defaults
}


def _reset_fake() -> None:
    global _calls_user, _calls_public
    global _last_auth_header_user, _last_auth_header_public
    _calls_user = 0
    _calls_public = 0
    _last_auth_header_user = None
    _last_auth_header_public = None


class _FakeHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        global _calls_user, _calls_public
        global _last_auth_header_user, _last_auth_header_public

        if self.path == "/api/v1/models/user":
            _calls_user += 1
            _last_auth_header_user = self.headers.get("Authorization")
            self._handle_user()
        elif self.path == "/api/v1/models":
            _calls_public += 1
            _last_auth_header_public = self.headers.get("Authorization")
            self._handle_public()
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_user(self) -> None:
        if _fake_mode == "auth_401":
            self._json(401, {"error": "unauthorized"})
        elif _fake_mode == "auth_403":
            self._json(403, {"error": "forbidden"})
        elif _fake_mode == "server_error":
            self._json(500, {"error": "internal server error"})
        elif _fake_mode == "malformed_data":
            self._json(200, {"not_data": "oops"})
        else:
            self._json(200, {"data": [FAKE_MODEL_FULL, FAKE_MODEL_MINIMAL]})

    def _handle_public(self) -> None:
        if _fake_mode == "public_401":
            self._json(401, {"error": "unauthorized"})
        else:
            self._json(200, {"data": [FAKE_MODEL_FULL, FAKE_MODEL_MINIMAL]})

    def _json(self, code: int, data: dict) -> None:
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args) -> None:  # noqa: ARG002
        pass  # Silence request logging


# ── DB helpers ────────────────────────────────────────────────────────────────
_db_path = os.path.join(BACKEND_DIR, "app.db")
_db_existed = os.path.exists(_db_path)


# ── Keyring backup ───────────────────────────────────────────────────────────
import keyring        # noqa: E402
import keyring.errors  # noqa: E402

_KR_SERVICE = "chatbot_interface"
_KR_API_KEY = "openrouter_api_key"
_KR_PROXY_URL = "proxy_url"

_saved_api_key = keyring.get_password(_KR_SERVICE, _KR_API_KEY)
_saved_proxy_url = keyring.get_password(_KR_SERVICE, _KR_PROXY_URL)

# DB settings backup
_saved_proxy_required: str | None = None
_saved_proxy_alias: str | None = None
if _db_existed:
    try:
        with sqlite3.connect(_db_path) as _con:
            _con.row_factory = sqlite3.Row
            _r = _con.execute(
                "SELECT value FROM settings WHERE key='proxy_required'"
            ).fetchone()
            _saved_proxy_required = _r["value"] if _r else None
            _r = _con.execute(
                "SELECT value FROM settings WHERE key='proxy_alias'"
            ).fetchone()
            _saved_proxy_alias = _r["value"] if _r else None
    except Exception:
        pass

# ── Server setup ─────────────────────────────────────────────────────────────
_uvicorn_name = "uvicorn.exe" if sys.platform == "win32" else "uvicorn"
UVICORN_EXE = os.path.join(os.path.dirname(sys.executable), _uvicorn_name)

server_proc: subprocess.Popen | None = None
fake_server: http.server.HTTPServer | None = None

# ── Port preflight ───────────────────────────────────────────────────────────
try:
    urllib.request.urlopen(f"{BASE}/healthz", timeout=1)
    print("  [FATAL] port_8787_already_in_use - another server is running.")
    sys.exit(1)
except Exception:
    pass

try:
    # ── Start fake OpenRouter server ──────────────────────────────────────
    fake_server = http.server.HTTPServer(
        ("127.0.0.1", FAKE_PORT), _FakeHandler,
    )
    fake_thread = threading.Thread(target=fake_server.serve_forever, daemon=True)
    fake_thread.start()

    # ── Start backend with fake OpenRouter URL ────────────────────────────
    env = os.environ.copy()
    env["OPENROUTER_BASE_URL"] = f"http://127.0.0.1:{FAKE_PORT}/api/v1"

    server_proc = subprocess.Popen(
        [UVICORN_EXE, "main:app",
         "--host", "127.0.0.1", "--port", "8787",
         "--log-level", "warning"],
        cwd=BACKEND_DIR,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Poll until ready (max 6 s)
    started = False
    for _ in range(30):
        time.sleep(0.2)
        try:
            urllib.request.urlopen(f"{BASE}/healthz", timeout=1)
            started = True
            break
        except Exception:
            pass

    if not started:
        print("  [FATAL] Backend did not start within 6 seconds.")
        sys.exit(1)

    TEST_KEY = "sk-test-phase5a-key"

    # ══════════════════════════════════════════════════════════════════════
    # V-Route  Route inventory
    # ══════════════════════════════════════════════════════════════════════
    section("V-Route  Route inventory")

    code, openapi = http_get("/openapi.json")
    path_keys = sorted(openapi.get("paths", {}).keys()) if openapi else []

    check("V-Route-1  exactly 12 path keys",
          len(path_keys) == 12,
          f"got: {', '.join(path_keys)}")

    settings_paths = [
        "/api/v1/settings", "/api/v1/settings/api-key",
        "/api/v1/settings/proxy", "/api/v1/settings/proxy/health",
    ]
    check("V-Route-2  all 4 settings paths present",
          all(p in path_keys for p in settings_paths))

    char_paths = [
        "/api/v1/characters", "/api/v1/characters/import",
        "/api/v1/characters/{character_id}",
    ]
    check("V-Route-3  all 3 characters paths present",
          all(p in path_keys for p in char_paths))

    chat_paths = [
        "/api/v1/chats", "/api/v1/chats/{chat_id}",
        "/api/v1/chats/{chat_id}/messages",
    ]
    check("V-Route-4  all 3 chats paths present",
          all(p in path_keys for p in chat_paths))

    models_info = (openapi or {}).get("paths", {}).get(
        "/api/v1/models/openrouter", {},
    )
    models_methods = sorted(models_info.keys())
    check("V-Route-5  /models/openrouter methods = GET only",
          models_methods == ["get"],
          f"got: {models_methods}")

    check("V-Route-6  no path contains 'completions'",
          not any("completions" in p for p in path_keys))

    check("V-Route-7a  /api/v1/chats/{chat_id}/complete registered (Phase 5B+)",
          "/api/v1/chats/{chat_id}/complete" in path_keys)
    check("V-Route-7b  /api/v1/completions NOT registered",
          "/api/v1/completions" not in path_keys)

    check("V-Route-8  no /api/v1/api/v1 double prefix",
          not any("/api/v1/api/v1" in p for p in path_keys))

    code, body = http_get("/healthz")
    check("V-Route-9  /healthz responds 200 + ok=true",
          code == 200 and body == {"ok": True},
          f"code={code}")

    # ══════════════════════════════════════════════════════════════════════
    # V-NoKey  No API key → public models
    # ══════════════════════════════════════════════════════════════════════
    section("V-NoKey  No API key → public models")

    http_delete("/api/v1/settings/api-key")
    _reset_fake()

    code, data = http_get("/api/v1/models/openrouter?refresh=true")
    check("V-NoKey-1  returns 200", code == 200, f"code={code}")
    check("V-NoKey-2  source=public",
          isinstance(data, dict) and data.get("source") == "public",
          f"source={data.get('source') if data else '?'}")
    check("V-NoKey-3  cached=false",
          isinstance(data, dict) and data.get("cached") is False)
    check("V-NoKey-4  count > 0",
          isinstance(data, dict) and isinstance(data.get("count"), int)
          and data["count"] > 0,
          f"count={data.get('count') if data else '?'}")
    check("V-NoKey-5  models is list",
          isinstance(data, dict) and isinstance(data.get("models"), list))
    check("V-NoKey-6  /models was called",
          _calls_public >= 1, f"calls={_calls_public}")
    check("V-NoKey-7  /models/user was NOT called",
          _calls_user == 0, f"calls={_calls_user}")

    # ══════════════════════════════════════════════════════════════════════
    # V-Cache  Cache behavior
    # ══════════════════════════════════════════════════════════════════════
    section("V-Cache  Cache behavior")

    prev_public = _calls_public
    code, data2 = http_get("/api/v1/models/openrouter")
    check("V-Cache-1  cached=true on second call",
          isinstance(data2, dict) and data2.get("cached") is True)
    check("V-Cache-2  fake server not called again",
          _calls_public == prev_public,
          f"before={prev_public}, after={_calls_public}")

    code, data3 = http_get("/api/v1/models/openrouter?refresh=true")
    check("V-Cache-3  refresh=true → cached=false",
          isinstance(data3, dict) and data3.get("cached") is False)
    check("V-Cache-4  fake server called again on refresh",
          _calls_public > prev_public,
          f"calls={_calls_public}")

    # Settings change invalidates cache
    prev_public2 = _calls_public
    http_delete("/api/v1/settings/api-key")  # triggers invalidate_model_cache
    code, data4 = http_get("/api/v1/models/openrouter")
    check("V-Cache-5  cache invalidated after settings change",
          isinstance(data4, dict) and data4.get("cached") is False)

    # ══════════════════════════════════════════════════════════════════════
    # V-Auth  API key → user models
    # ══════════════════════════════════════════════════════════════════════
    section("V-Auth  API key → user models")

    _fake_mode = "normal"
    http_post("/api/v1/settings/api-key", {"api_key": TEST_KEY})
    _reset_fake()

    code, data = http_get("/api/v1/models/openrouter?refresh=true")
    check("V-Auth-1  returns 200", code == 200, f"code={code}")
    check("V-Auth-2  source=user",
          isinstance(data, dict) and data.get("source") == "user")
    check("V-Auth-3  /models/user called",
          _calls_user >= 1, f"calls={_calls_user}")
    check("V-Auth-4  Authorization header correct",
          _last_auth_header_user == f"Bearer {TEST_KEY}")
    check("V-Auth-5  API key not in any response body",
          all(TEST_KEY not in r for r in _all_responses))

    # ══════════════════════════════════════════════════════════════════════
    # V-AuthFail  Auth failure handling
    # ══════════════════════════════════════════════════════════════════════
    section("V-AuthFail  Auth failure handling")

    # ── 401 ───────────────────────────────────────────────────────────────
    _fake_mode = "auth_401"
    _reset_fake()

    code, data = http_get("/api/v1/models/openrouter?refresh=true")
    check("V-AuthFail-1  401 → HTTP 401", code == 401, f"code={code}")
    check("V-AuthFail-2  detail=api_key_invalid",
          isinstance(data, dict) and data.get("detail") == "api_key_invalid")
    check("V-AuthFail-3  /models/user was called",
          _calls_user >= 1, f"calls={_calls_user}")
    check("V-AuthFail-4  /models NOT called after auth fail",
          _calls_public == 0, f"calls={_calls_public}")

    # ── 403 ───────────────────────────────────────────────────────────────
    _fake_mode = "auth_403"
    _reset_fake()

    code, data = http_get("/api/v1/models/openrouter?refresh=true")
    check("V-AuthFail-5  403 → HTTP 401", code == 401, f"code={code}")
    check("V-AuthFail-6  /models NOT called after 403",
          _calls_public == 0, f"calls={_calls_public}")

    # ══════════════════════════════════════════════════════════════════════
    # V-Pub401  Public /models returns 401 (no API key)
    # ══════════════════════════════════════════════════════════════════════
    section("V-Pub401  Public /models returns 401")

    http_delete("/api/v1/settings/api-key")
    _fake_mode = "public_401"
    _reset_fake()

    code, data = http_get("/api/v1/models/openrouter?refresh=true")
    check("V-Pub401-1  returns HTTP 401", code == 401, f"code={code}")
    check("V-Pub401-2  detail=api_key_required_by_openrouter",
          isinstance(data, dict)
          and data.get("detail") == "api_key_required_by_openrouter")
    check("V-Pub401-3  raw error body not returned",
          isinstance(data, dict) and "unauthorized" not in json.dumps(data))
    check("V-Pub401-4  /models/user was NOT called (no key)",
          _calls_user == 0, f"calls={_calls_user}")
    check("V-Pub401-5  /models was called",
          _calls_public >= 1, f"calls={_calls_public}")

    # Verify it is not cached - a subsequent call with normal mode should succeed
    _fake_mode = "normal"
    _reset_fake()
    code2, data2 = http_get("/api/v1/models/openrouter?refresh=true")
    check("V-Pub401-6  error was not cached (next call succeeds)",
          code2 == 200 and isinstance(data2, dict)
          and data2.get("source") == "public")

    # Restore API key for subsequent tests
    http_post("/api/v1/settings/api-key", {"api_key": TEST_KEY})

    # ══════════════════════════════════════════════════════════════════════
    # V-Fallback  Non-auth failure → public fallback
    # ══════════════════════════════════════════════════════════════════════
    section("V-Fallback  Non-auth failure → public fallback")

    _fake_mode = "server_error"
    _reset_fake()

    code, data = http_get("/api/v1/models/openrouter?refresh=true")
    check("V-Fallback-1  returns 200", code == 200, f"code={code}")
    check("V-Fallback-2  source=public_fallback",
          isinstance(data, dict) and data.get("source") == "public_fallback")
    check("V-Fallback-3  fallback_reason present",
          isinstance(data, dict) and data.get("fallback_reason") is not None,
          f"reason={data.get('fallback_reason') if data else '?'}")
    check("V-Fallback-4  /models/user was called first",
          _calls_user >= 1, f"calls={_calls_user}")
    check("V-Fallback-5  /models was called (fallback)",
          _calls_public >= 1, f"calls={_calls_public}")
    check("V-Fallback-6  public fallback has no Authorization",
          _last_auth_header_public is None,
          f"got={_last_auth_header_public}")

    # ══════════════════════════════════════════════════════════════════════
    # V-Malformed  Malformed /models/user response
    # ══════════════════════════════════════════════════════════════════════
    section("V-Malformed  Malformed /models/user response")

    _fake_mode = "malformed_data"
    _reset_fake()

    code, data = http_get("/api/v1/models/openrouter?refresh=true")
    check("V-Malformed-1  returns HTTP 502", code == 502, f"code={code}")
    check("V-Malformed-2  detail=invalid_openrouter_models_response",
          isinstance(data, dict)
          and data.get("detail") == "invalid_openrouter_models_response")
    check("V-Malformed-3  /models NOT called (no fallback on malformed)",
          _calls_public == 0, f"calls={_calls_public}")

    # ══════════════════════════════════════════════════════════════════════
    # V-ProxyGate  Proxy gate enforcement
    # ══════════════════════════════════════════════════════════════════════
    section("V-ProxyGate  Proxy gate enforcement")

    _fake_mode = "normal"
    # Clean proxy state and set proxy_required=true via direct DB
    http_delete("/api/v1/settings/proxy")
    time.sleep(0.1)
    with sqlite3.connect(_db_path) as _con:
        _con.execute(
            "INSERT OR REPLACE INTO settings (key, value) "
            "VALUES ('proxy_required', '1')"
        )
        _con.commit()
    _reset_fake()

    code, data = http_get("/api/v1/models/openrouter?refresh=true")
    check("V-ProxyGate-1  returns HTTP 503", code == 503, f"code={code}")
    check("V-ProxyGate-2  reason=proxy_missing",
          isinstance(data, dict) and data.get("detail") == "proxy_missing")
    check("V-ProxyGate-3  fake server received 0 requests",
          _calls_user + _calls_public == 0,
          f"user={_calls_user}, public={_calls_public}")

    # Cleanup proxy_required
    with sqlite3.connect(_db_path) as _con:
        _con.execute(
            "INSERT OR REPLACE INTO settings (key, value) "
            "VALUES ('proxy_required', '0')"
        )
        _con.commit()

    # ══════════════════════════════════════════════════════════════════════
    # V-Norm  Normalization
    # ══════════════════════════════════════════════════════════════════════
    section("V-Norm  Normalization")

    _fake_mode = "normal"
    http_delete("/api/v1/settings/api-key")
    _reset_fake()

    code, data = http_get("/api/v1/models/openrouter?refresh=true")
    models = data.get("models", []) if isinstance(data, dict) else []

    EXPECTED_KEYS = {
        "id", "name", "description", "context_length",
        "max_completion_tokens", "supported_parameters",
        "input_modalities", "output_modalities", "pricing",
        "top_provider", "created", "canonical_slug",
    }

    check("V-Norm-1  2 models returned",
          len(models) == 2, f"count={len(models)}")

    all_12 = all(set(m.keys()) == EXPECTED_KEYS for m in models) if models else False
    check("V-Norm-2  each model has exactly 12 keys", all_12,
          f"keys={sorted(models[0].keys()) if models else '?'}")

    minimal = next((m for m in models if m.get("id") == "test/minimal-model"), None)
    if minimal:
        check("V-Norm-3  missing supported_parameters → []",
              minimal.get("supported_parameters") == [])
        check("V-Norm-4  missing input_modalities → []",
              minimal.get("input_modalities") == [])
        check("V-Norm-5  missing output_modalities → []",
              minimal.get("output_modalities") == [])
        check("V-Norm-6  missing pricing → {}",
              minimal.get("pricing") == {})
        check("V-Norm-7  missing top_provider → {}",
              minimal.get("top_provider") == {})
    else:
        for n in range(3, 8):
            check(f"V-Norm-{n}  (skipped - no minimal model)", False, "missing")

    full = next((m for m in models if m.get("id") == "openai/gpt-4"), None)
    if full:
        check("V-Norm-8  max_completion_tokens from top_provider",
              full.get("max_completion_tokens") == 4096)
        check("V-Norm-9  context_length from top-level",
              full.get("context_length") == 128000)
    else:
        check("V-Norm-8  (skipped - no full model)", False, "missing")
        check("V-Norm-9  (skipped - no full model)", False, "missing")

    check("V-Norm-10  no 'raw' field in any model",
          all("raw" not in m for m in models))

    # ══════════════════════════════════════════════════════════════════════
    # V-Src  Static source analysis
    # ══════════════════════════════════════════════════════════════════════
    section("V-Src  Static source analysis")

    mr_path = os.path.join(BACKEND_DIR, "routers", "models_router.py")
    mr_raw = open(mr_path, encoding="utf-8").read()
    main_path = os.path.join(BACKEND_DIR, "main.py")
    main_code = open(main_path, encoding="utf-8").read()

    # Strip comments and docstrings from models_router.py for import checks
    _mr_lines = []
    _mr_in_doc = False
    for line in mr_raw.splitlines():
        stripped = line.strip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            if stripped.count('"""') == 1 or stripped.count("'''") == 1:
                _mr_in_doc = not _mr_in_doc
            continue
        if _mr_in_doc:
            continue
        if stripped.startswith("#"):
            continue
        _mr_lines.append(stripped)
    mr_code = "\n".join(_mr_lines)

    check("V-Src-1  models_router.py has no 'httpx'",
          "httpx" not in mr_code)
    check("V-Src-2  models_router.py has no 'requests'",
          "requests" not in mr_code)
    check("V-Src-3  models_router.py has no 'urllib.request'",
          "urllib.request" not in mr_code)
    check("V-Src-4  models_router.py has no 'keyring'",
          "keyring" not in mr_code)

    # main.py active router checks
    main_lines = [ln.strip() for ln in main_code.splitlines()
                  if ln.strip() and not ln.strip().startswith("#")]
    check("V-Src-5  main.py has active settings router import",
          any("from routers import settings" in ln for ln in main_lines))
    check("V-Src-6  main.py has active characters router import",
          any("from routers import characters" in ln for ln in main_lines))
    check("V-Src-7  main.py has active chats router import",
          any("from routers import chats" in ln for ln in main_lines))
    check("V-Src-8  main.py has active models_router import",
          any("from routers import models_router" in ln for ln in main_lines))
    check("V-Src-9  main.py has active completions router (Phase 5B+)",
          any("from routers import completions" in ln for ln in main_lines))

    # ══════════════════════════════════════════════════════════════════════
    # V-Sec  Global secret / leak safety
    # ══════════════════════════════════════════════════════════════════════
    section("V-Sec  Global secret / leak safety")

    all_bodies = " ".join(_all_responses)

    check("V-Sec-1  TEST_KEY absent from all response bodies",
          TEST_KEY not in all_bodies)

    # Proxy URL - we didn't set one explicitly, but verify no proxy-related
    # secret leaks from keyring backup
    if _saved_proxy_url:
        check("V-Sec-2  saved proxy URL absent from all responses",
              _saved_proxy_url not in all_bodies)
    else:
        check("V-Sec-2  no proxy URL to leak (none was saved)", True)

    # Raw fake OpenRouter error bodies must not be returned verbatim
    check("V-Sec-3  raw fake 'unauthorized' error not in responses",
          '"error": "unauthorized"' not in all_bodies
          and '"error":"unauthorized"' not in all_bodies)
    check("V-Sec-4  raw fake 'forbidden' error not in responses",
          '"error": "forbidden"' not in all_bodies
          and '"error":"forbidden"' not in all_bodies)
    check("V-Sec-5  raw fake 'internal server error' not in responses",
          '"error": "internal server error"' not in all_bodies
          and '"error":"internal server error"' not in all_bodies)

finally:
    # ── Cleanup ───────────────────────────────────────────────────────────
    if server_proc and server_proc.poll() is None:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            server_proc.kill()
            server_proc.wait()

    if fake_server:
        fake_server.shutdown()
        fake_server.server_close()

    # ── Restore keyring ───────────────────────────────────────────────────
    try:
        if _saved_api_key is not None:
            keyring.set_password(_KR_SERVICE, _KR_API_KEY, _saved_api_key)
        else:
            try:
                keyring.delete_password(_KR_SERVICE, _KR_API_KEY)
            except keyring.errors.PasswordDeleteError:
                pass
    except Exception:
        pass

    try:
        if _saved_proxy_url is not None:
            keyring.set_password(_KR_SERVICE, _KR_PROXY_URL, _saved_proxy_url)
        else:
            try:
                keyring.delete_password(_KR_SERVICE, _KR_PROXY_URL)
            except keyring.errors.PasswordDeleteError:
                pass
    except Exception:
        pass

    # ── Restore DB settings ───────────────────────────────────────────────
    time.sleep(0.3)
    if _db_existed:
        try:
            with sqlite3.connect(_db_path) as _con:
                if _saved_proxy_required is not None:
                    _con.execute(
                        "INSERT OR REPLACE INTO settings (key, value) "
                        "VALUES ('proxy_required', ?)",
                        (_saved_proxy_required,),
                    )
                else:
                    _con.execute(
                        "DELETE FROM settings WHERE key='proxy_required'"
                    )
                if _saved_proxy_alias is not None:
                    _con.execute(
                        "INSERT OR REPLACE INTO settings (key, value) "
                        "VALUES ('proxy_alias', ?)",
                        (_saved_proxy_alias,),
                    )
                else:
                    _con.execute(
                        "DELETE FROM settings WHERE key='proxy_alias'"
                    )
                _con.commit()
        except Exception:
            pass
        print("  [info] Keyring and DB settings restored.")
    else:
        for ext in ("", "-wal", "-shm"):
            p = _db_path + ext
            if os.path.exists(p):
                try:
                    os.remove(p)
                except PermissionError:
                    pass
        print("  [info] Test-only app.db removed.")

# ── Summary ───────────────────────────────────────────────────────────────────
section("Summary")

passed = sum(1 for _, ok in results if ok)
total = len(results)
print(f"  {passed}/{total} checks passed\n")
if passed < total:
    print("  FAILED:")
    for label, ok in results:
        if not ok:
            print(f"    x {label}")
    print()

sys.exit(0 if passed == total else 1)
