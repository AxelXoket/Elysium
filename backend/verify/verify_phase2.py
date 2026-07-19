"""
Phase 2 verification script (Settings Router).
Run from backend/ with the virtual environment active:
    .venv/Scripts/python verify_phase2.py

Safety guarantees:
  - Keyring API key and proxy URL are backed up before tests and restored after.
  - If app.db existed before the run, its settings rows are restored.
  - If app.db did not exist, it is deleted after the run.
  - No secret values are printed.
"""

import sys
import os
import json
import time
import sqlite3
import subprocess
import urllib.request
import urllib.error

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
_all_response_bodies: list[str] = []

def check(label: str, ok: bool, detail: str = "") -> None:
    tag = PASS if ok else FAIL
    msg = f"  [{tag}] {label}"
    if detail:
        msg += f"  ->  {detail}"
    print(msg)
    results.append((label, ok))

def section(title: str) -> None:
    print(f"\n{'-'*62}\n  {title}\n{'-'*62}")

# ── HTTP helpers ──────────────────────────────────────────────────────────────
BASE = "http://127.0.0.1:8787"

def http_get(path: str, timeout: float = 5) -> tuple[int, dict]:
    url = BASE + path
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode()
            _all_response_bodies.append(body)
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        _all_response_bodies.append(body)
        return e.code, json.loads(body) if body else {}

def http_post(path: str, data: dict) -> tuple[int, dict]:
    url = BASE + path
    payload = json.dumps(data).encode()
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode()
            _all_response_bodies.append(body)
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        _all_response_bodies.append(body)
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, {"raw": body}

def http_delete(path: str) -> tuple[int, dict]:
    url = BASE + path
    req = urllib.request.Request(url, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode()
            _all_response_bodies.append(body)
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        _all_response_bodies.append(body)
        return e.code, json.loads(body) if body else {}

def _read_all_settings_values() -> list[str]:
    """Read all values from the SQLite settings table (test process side)."""
    try:
        with sqlite3.connect(_db_path) as con:
            return [r[1] for r in con.execute("SELECT key, value FROM settings").fetchall()]
    except Exception:
        return []

def _read_settings_dict() -> dict[str, str]:
    """Read all settings rows as {key: value}."""
    try:
        with sqlite3.connect(_db_path) as con:
            return {r[0]: r[1] for r in con.execute("SELECT key, value FROM settings").fetchall()}
    except Exception:
        return {}

# ── Test sentinels (not real credentials) ─────────────────────────────────────
TEST_API_KEY = "sk-or-v2-verify-test-key-not-real"
TEST_PROXY_URL = "socks5://127.0.0.1:9999"
TEST_PROXY_ALIAS = "test-alias"

# ── Keyring imports ───────────────────────────────────────────────────────────
from config import KEYRING_API_KEY, KEYRING_PROXY_URL
from keyring_service import get_secret, set_secret, delete_secret

# ── Backup keyring secrets ────────────────────────────────────────────────────
_bak_api_key = get_secret(KEYRING_API_KEY)
_bak_proxy_url = get_secret(KEYRING_PROXY_URL)

# ── Backup app.db settings rows ──────────────────────────────────────────────
_db_path = os.path.join(BACKEND_DIR, "app.db")
_db_existed = os.path.exists(_db_path)
_bak_settings: list[tuple] = []
if _db_existed:
    with sqlite3.connect(_db_path) as _con:
        _bak_settings = _con.execute("SELECT key, value FROM settings").fetchall()

# ── Server subprocess ─────────────────────────────────────────────────────────
_uvicorn_name = "uvicorn.exe" if sys.platform == "win32" else "uvicorn"
UVICORN_EXE = os.path.join(os.path.dirname(sys.executable), _uvicorn_name)

server_proc: subprocess.Popen | None = None

try:
    server_proc = subprocess.Popen(
        [UVICORN_EXE, "main:app",
         "--host", "127.0.0.1", "--port", "8787",
         "--log-level", "warning"],
        cwd=BACKEND_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Poll until server is ready (max 6 s).
    started = False
    for _ in range(30):
        time.sleep(0.2)
        try:
            urllib.request.urlopen(BASE + "/healthz", timeout=1)
            started = True
            break
        except Exception:
            continue

    if not started:
        print("FATAL: server did not start within 6 seconds.")
        sys.exit(2)

    # ==========================================================================
    # V-Route: Route inventory
    # ==========================================================================
    section("V-Route  Route inventory")

    _, openapi = http_get("/openapi.json")
    paths = list(openapi.get("paths", {}).keys())
    paths_str = ", ".join(sorted(paths))

    expected_paths = {
        "/api/v1/settings",
        "/api/v1/settings/api-key",
        "/api/v1/settings/proxy",
        "/api/v1/settings/proxy/health",
    }
    check("V-Route-1  openapi.json contains all 4 settings path keys",
          expected_paths <= set(paths),
          f"got: {paths_str}")

    check("V-Route-2  /healthz responds 200 (not in schema)",
          started, "confirmed during startup poll")

    check("V-Route-3  no double-prefix /api/v1/api/v1",
          not any("/api/v1/api/v1" in p for p in paths),
          f"paths: {paths_str}")

    check("V-Route-4  /api/v1/characters registered (Phase 3+)",
          any("characters" in p for p in paths))

    check("V-Route-5  /api/v1/chats registered (Phase 4+)",
          any("chats" in p for p in paths))

    check("V-Route-6  /api/v1/models/openrouter registered (Phase 5A+)",
          any("/models/openrouter" in p for p in paths))

    # ==========================================================================
    # V-HZ: Explicit /healthz check
    # ==========================================================================
    section("V-HZ  Explicit /healthz")

    code, data = http_get("/healthz")
    check("V-HZ-1  GET /healthz returns 200",
          code == 200, f"code={code}")
    check("V-HZ-2  body is exactly {\"ok\": true}",
          data == {"ok": True}, f"got={data}")

    # ==========================================================================
    # V1-V10: Settings endpoint tests
    # ==========================================================================
    section("V1-V10  Settings endpoint tests")

    # Clear test state first
    delete_secret(KEYRING_API_KEY)
    delete_secret(KEYRING_PROXY_URL)

    # ── V1: GET /settings baseline ────────────────────────────────────────────
    code, data = http_get("/api/v1/settings")
    expected_fields = {"api_key_set", "proxy_required", "proxy_configured", "proxy_alias"}
    check("V1a GET /settings returns exactly 4 keys (no extras)",
          code == 200 and set(data.keys()) == expected_fields,
          f"keys={sorted(data.keys())}")

    check("V1b api_key_set is bool",
          isinstance(data.get("api_key_set"), bool),
          f"type={type(data.get('api_key_set')).__name__}")
    check("V1c proxy_required is bool",
          isinstance(data.get("proxy_required"), bool),
          f"type={type(data.get('proxy_required')).__name__}")
    check("V1d proxy_configured is bool",
          isinstance(data.get("proxy_configured"), bool),
          f"type={type(data.get('proxy_configured')).__name__}")
    check("V1e proxy_alias is str or null",
          data.get("proxy_alias") is None or isinstance(data.get("proxy_alias"), str),
          f"type={type(data.get('proxy_alias')).__name__}")

    # ── V2: POST + verify api-key ─────────────────────────────────────────────
    code, data = http_post("/api/v1/settings/api-key", {"api_key": TEST_API_KEY})
    check("V2a POST /settings/api-key -> ok=true",
          code == 200 and data.get("ok") is True, f"code={code}")

    code, data = http_get("/api/v1/settings")
    check("V2b GET /settings -> api_key_set=true",
          data.get("api_key_set") is True)

    # Verify keyring contains the exact test key
    kr_api = get_secret(KEYRING_API_KEY)
    check("V2c keyring contains exact test API key",
          kr_api == TEST_API_KEY)

    # Verify API key is NOT anywhere in SQLite settings table
    all_vals = _read_all_settings_values()
    check("V2d API key NOT in SQLite settings table",
          TEST_API_KEY not in all_vals,
          f"settings_values_count={len(all_vals)}")

    # Verify GET /settings response has no extra secret-like field
    check("V2e GET /settings has exactly 4 keys after api-key save",
          set(data.keys()) == expected_fields)

    # ── V3: DELETE + verify api-key ───────────────────────────────────────────
    code, data = http_delete("/api/v1/settings/api-key")
    check("V3a DELETE /settings/api-key -> ok=true",
          code == 200 and data.get("ok") is True)

    code, data = http_get("/api/v1/settings")
    check("V3b GET /settings -> api_key_set=false",
          data.get("api_key_set") is False)

    # Verify keyring is actually cleared
    check("V3c keyring API key is None after delete",
          get_secret(KEYRING_API_KEY) is None)

    # Second DELETE must be idempotent
    code, data = http_delete("/api/v1/settings/api-key")
    check("V3d second DELETE /settings/api-key still ok",
          code == 200 and data.get("ok") is True)

    # ── V4: POST /settings/proxy ──────────────────────────────────────────────
    code, data = http_post("/api/v1/settings/proxy", {
        "proxy_url": TEST_PROXY_URL,
        "proxy_required": True,
        "proxy_alias": TEST_PROXY_ALIAS,
    })
    check("V4a POST /settings/proxy -> ok=true",
          code == 200 and data.get("ok") is True)

    # Check keyring (OS keyring is shared between processes)
    kr_value = get_secret(KEYRING_PROXY_URL)
    check("V4b proxy URL saved in keyring",
          kr_value == TEST_PROXY_URL)

    # Verify proxy URL is NOT anywhere in SQLite settings table
    all_vals = _read_all_settings_values()
    check("V4c proxy URL NOT in SQLite settings table",
          TEST_PROXY_URL not in all_vals)

    # Verify proxy_required stored as "0" or "1" only
    settings_dict = _read_settings_dict()
    pr_val = settings_dict.get("proxy_required")
    check("V4d proxy_required stored as '0' or '1'",
          pr_val in ("0", "1"), f"got={pr_val!r}")

    # Verify proxy_alias stored in SQLite
    check("V4e proxy_alias stored in SQLite",
          "proxy_alias" in settings_dict,
          f"value={settings_dict.get('proxy_alias')!r}")

    # Verify proxy_url key NOT stored in SQLite
    check("V4f 'proxy_url' key NOT in SQLite settings",
          "proxy_url" not in settings_dict)

    # ── V5: GET /settings after proxy save ────────────────────────────────────
    code, data = http_get("/api/v1/settings")
    check("V5  GET /settings -> proxy_configured=true, alias=test-alias",
          data.get("proxy_configured") is True
          and data.get("proxy_alias") == TEST_PROXY_ALIAS
          and data.get("proxy_required") is True,
          f"got={data}")

    # ── V6: DELETE /settings/proxy ────────────────────────────────────────────
    code, data = http_delete("/api/v1/settings/proxy")
    check("V6a DELETE /settings/proxy -> ok=true",
          code == 200 and data.get("ok") is True)

    code, data = http_get("/api/v1/settings")
    check("V6b GET /settings -> proxy_configured=false, proxy_required=false",
          data.get("proxy_configured") is False
          and data.get("proxy_required") is False,
          f"got={data}")

    # DELETE /settings/proxy must be idempotent
    code2, data2 = http_delete("/api/v1/settings/proxy")
    check("V6c second DELETE /settings/proxy still ok",
          code2 == 200 and data2.get("ok") is True)

    code3, data3 = http_get("/api/v1/settings")
    check("V6d state still correct after second delete",
          data3.get("proxy_configured") is False
          and data3.get("proxy_required") is False,
          f"got={data3}")

    # ── V7: proxy_required=true + no URL -> proxy_missing ─────────────────────
    http_post("/api/v1/settings/proxy", {
        "proxy_url": TEST_PROXY_URL,
        "proxy_required": True,
        "proxy_alias": "",
    })
    # Delete the URL from keyring (shared OS keyring - affects server process too)
    delete_secret(KEYRING_PROXY_URL)

    code, data = http_get("/api/v1/settings/proxy/health")
    check("V7  proxy_required=true + no URL -> reason=proxy_missing",
          data.get("reason") == "proxy_missing" and data.get("healthy") is False,
          f"got={data}")

    # Cleanup
    http_delete("/api/v1/settings/proxy")

    # ── V8: Health cache behaviour ────────────────────────────────────────────
    # First call after delete -> fresh (no proxy -> healthy, cached=False)
    code, data1 = http_get("/api/v1/settings/proxy/health")
    check("V8a first health call -> cached=false",
          data1.get("cached") is False, f"got={data1}")

    code, data2 = http_get("/api/v1/settings/proxy/health")
    check("V8b second health call -> cached=true",
          data2.get("cached") is True, f"got={data2}")

    # POST /proxy invalidates cache
    http_post("/api/v1/settings/proxy", {
        "proxy_url": TEST_PROXY_URL,
        "proxy_required": False,
        "proxy_alias": "",
    })
    code, data3 = http_get("/api/v1/settings/proxy/health")
    check("V8c health after POST /proxy -> cached=false (invalidated)",
          data3.get("cached") is False, f"got={data3}")

    # Cleanup
    http_delete("/api/v1/settings/proxy")

    # ── V9: Proxy validation errors ───────────────────────────────────────────
    code, data = http_post("/api/v1/settings/proxy", {
        "proxy_url": "ftp://example.com:8080",
        "proxy_required": False,
    })
    check("V9a ftp scheme -> 400 invalid_proxy_scheme",
          code == 400 and "invalid_proxy_scheme" in json.dumps(data),
          f"code={code}")

    code, data = http_post("/api/v1/settings/proxy", {
        "proxy_url": "socks5://",
        "proxy_required": False,
    })
    check("V9b socks5:// (no host) -> 400 proxy_url_invalid",
          code == 400 and "proxy_url_invalid" in json.dumps(data),
          f"code={code}")

    code, data = http_post("/api/v1/settings/proxy", {
        "proxy_url": "",
        "proxy_required": False,
    })
    check("V9c empty string -> 400 proxy_url_required",
          code == 400 and "proxy_url_required" in json.dumps(data),
          f"code={code}")

    code, data = http_post("/api/v1/settings/proxy", {
        "proxy_url": "   ",
        "proxy_required": False,
    })
    check("V9d whitespace-only -> 400 proxy_url_required",
          code == 400 and "proxy_url_required" in json.dumps(data),
          f"code={code}")

    # ── V10: proxy_alias normalization ─────────────────────────────────────────
    http_post("/api/v1/settings/proxy", {
        "proxy_url": TEST_PROXY_URL,
        "proxy_required": False,
        "proxy_alias": None,
    })
    code, data = http_get("/api/v1/settings")
    check("V10a proxy_alias=null input -> GET returns proxy_alias=null",
          data.get("proxy_alias") is None,
          f"got={data.get('proxy_alias')!r}")

    http_post("/api/v1/settings/proxy", {
        "proxy_url": TEST_PROXY_URL,
        "proxy_required": False,
        "proxy_alias": "",
    })
    code, data = http_get("/api/v1/settings")
    check("V10b proxy_alias='' input -> GET returns proxy_alias=null",
          data.get("proxy_alias") is None,
          f"got={data.get('proxy_alias')!r}")

    http_post("/api/v1/settings/proxy", {
        "proxy_url": TEST_PROXY_URL,
        "proxy_required": False,
        "proxy_alias": "   ",
    })
    code, data = http_get("/api/v1/settings")
    check("V10c proxy_alias='   ' whitespace -> GET returns proxy_alias=null",
          data.get("proxy_alias") is None,
          f"got={data.get('proxy_alias')!r}")

    # Cleanup
    http_delete("/api/v1/settings/proxy")
    delete_secret(KEYRING_API_KEY)

    # ==========================================================================
    # V-Key: Invalid API key body + mutation safety
    # ==========================================================================
    section("V-Key  Invalid API key validation")

    # Ensure keyring is clean before invalid tests
    delete_secret(KEYRING_API_KEY)

    code, data = http_post("/api/v1/settings/api-key", {"api_key": ""})
    check("V-Key-1  api_key='' -> 400 or 422",
          code in (400, 422), f"code={code}")
    check("V-Key-2  keyring still None after empty api_key",
          get_secret(KEYRING_API_KEY) is None)

    code, data = http_post("/api/v1/settings/api-key", {"api_key": "   "})
    check("V-Key-3  api_key='   ' -> 400 or 422",
          code in (400, 422), f"code={code}")
    check("V-Key-4  keyring still None after whitespace api_key",
          get_secret(KEYRING_API_KEY) is None)

    # ==========================================================================
    # V-Prx: Invalid proxy mutation safety
    # ==========================================================================
    section("V-Prx  Invalid proxy mutation safety")

    # Ensure clean state
    delete_secret(KEYRING_PROXY_URL)

    # Store a known value in keyring to verify it's not overwritten
    set_secret(KEYRING_PROXY_URL, "socks5://known-safe-value:1234")

    code, _ = http_post("/api/v1/settings/proxy", {
        "proxy_url": "ftp://evil.com", "proxy_required": False,
    })
    check("V-Prx-1  invalid scheme does not overwrite keyring",
          get_secret(KEYRING_PROXY_URL) == "socks5://known-safe-value:1234",
          f"code={code}")

    code, _ = http_post("/api/v1/settings/proxy", {
        "proxy_url": "", "proxy_required": False,
    })
    check("V-Prx-2  empty URL does not overwrite keyring",
          get_secret(KEYRING_PROXY_URL) == "socks5://known-safe-value:1234")

    # Verify SQLite settings were not mutated by invalid requests
    settings_before = _read_settings_dict()
    code, _ = http_post("/api/v1/settings/proxy", {
        "proxy_url": "badscheme://x", "proxy_required": True,
    })
    settings_after = _read_settings_dict()
    check("V-Prx-3  invalid proxy does not mutate SQLite settings",
          settings_before == settings_after)

    # Clean up the known value
    delete_secret(KEYRING_PROXY_URL)

    # ==========================================================================
    # V-Scheme: socks5h valid scheme
    # ==========================================================================
    section("V-Scheme  socks5h acceptance")

    code, data = http_post("/api/v1/settings/proxy", {
        "proxy_url": "socks5h://127.0.0.1:9999",
        "proxy_required": False,
    })
    check("V-Scheme-1  socks5h://... returns 200",
          code == 200 and data.get("ok") is True, f"code={code}")
    # Cleanup
    http_delete("/api/v1/settings/proxy")

    # ==========================================================================
    # V-Unreach: proxy_required + unreachable proxy health
    # ==========================================================================
    section("V-Unreach  Unreachable proxy health")

    http_post("/api/v1/settings/proxy", {
        "proxy_url": TEST_PROXY_URL,   # socks5://127.0.0.1:9999 - not listening
        "proxy_required": True,
        "proxy_alias": "",
    })
    code, data = http_get("/api/v1/settings/proxy/health", timeout=15)
    check("V-Unreach-1  unreachable proxy -> healthy=false",
          data.get("healthy") is False, f"got={data}")
    check("V-Unreach-2  reason is proxy_unreachable or timeout",
          data.get("reason") in ("proxy_unreachable", "timeout"),
          f"reason={data.get('reason')!r}")

    # Cleanup
    http_delete("/api/v1/settings/proxy")

    # ==========================================================================
    # V-Direct: Optional-direct proxy health (no proxy, not required)
    # ==========================================================================
    section("V-Direct  Direct mode health")

    # Ensure clean: no proxy configured, proxy_required=false
    delete_secret(KEYRING_PROXY_URL)
    # Need to invalidate server's health cache - do a DELETE to trigger side-effects
    http_delete("/api/v1/settings/proxy")

    code, data = http_get("/api/v1/settings/proxy/health")
    check("V-Direct-1  proxy_required=false + no URL -> healthy=true",
          data.get("healthy") is True, f"got={data}")
    check("V-Direct-2  proxy_required=false + no URL -> reason=null",
          data.get("reason") is None, f"got={data}")

    # ==========================================================================
    # V-Cache: In-process cache invalidation
    # ==========================================================================
    section("V-Cache  Cache invalidation (in-process)")

    from openrouter import invalidate_model_cache, _model_cache
    from proxy_health import invalidate_health_cache, _cache as _health_cache

    # Model cache: seed -> invalidate -> verify empty
    _model_cache["data"] = [{"id": "test"}]
    _model_cache["fetched_at"] = time.monotonic()
    invalidate_model_cache()
    check("V-Cache-1  invalidate_model_cache() clears dict",
          len(_model_cache) == 0, f"len={len(_model_cache)}")

    # Health cache: seed -> invalidate -> verify empty
    _health_cache["result"] = {"healthy": True}
    _health_cache["fetched_at"] = time.monotonic()
    invalidate_health_cache()
    check("V-Cache-2  invalidate_health_cache() clears dict",
          len(_health_cache) == 0, f"len={len(_health_cache)}")

    # ==========================================================================
    # V-Src: Static source checks
    # ==========================================================================
    section("V-Src  Static source analysis")

    settings_src_path = os.path.join(BACKEND_DIR, "routers", "settings.py")
    with open(settings_src_path, "r", encoding="utf-8") as f:
        settings_src = f.read()

    # Extract code lines only (skip comments and docstring lines) for import checks.
    _code_lines = []
    _in_docstring = False
    for line in settings_src.splitlines():
        stripped = line.strip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            if stripped.count('"""') == 1 or stripped.count("'''") == 1:
                _in_docstring = not _in_docstring
            continue  # skip the delimiter line itself
        if _in_docstring:
            continue
        if stripped.startswith("#"):
            continue
        _code_lines.append(stripped)
    _code_text = "\n".join(_code_lines)

    check("V-Src-1  settings.py code has no httpx.AsyncClient",
          "httpx.AsyncClient" not in _code_text)
    check("V-Src-2  settings.py has no 'requests.'",
          "requests." not in _code_text)
    check("V-Src-3  settings.py has no 'urllib.request'",
          "urllib.request" not in _code_text)
    check("V-Src-4  settings.py has no 'openrouter.ai'",
          "openrouter.ai" not in _code_text)
    check("V-Src-5  settings.py has no fetch_models call",
          "fetch_models" not in _code_text)
    check("V-Src-6  settings.py has no complete call",
          "complete(" not in _code_text
          and "send_completion" not in _code_text)

    # Phase boundary: main.py must have settings router active, others commented
    main_src_path = os.path.join(BACKEND_DIR, "main.py")
    with open(main_src_path, "r", encoding="utf-8") as f:
        main_src = f.read()

    # Active settings router import
    check("V-Src-7  main.py has active settings router import",
          "from routers import settings" in main_src
          and "# from routers import settings" not in main_src)

    check("V-Src-8  main.py has active completions router (Phase 5B+)",
          any(
              rt in stripped and "import" in stripped and not stripped.startswith("#")
              for line in main_src.splitlines()
              for stripped in [line.strip()]
              for rt in ["completions"]
          ))

    # ==========================================================================
    # V-Sec: Secret exposure
    # ==========================================================================
    section("V-Sec  Secret exposure check")

    all_bodies = " ".join(_all_response_bodies)
    check("V-Sec-1  test API key absent from all responses",
          TEST_API_KEY not in all_bodies)
    check("V-Sec-2  test proxy URL absent from all responses",
          TEST_PROXY_URL not in all_bodies)

finally:
    # ── Stop server ───────────────────────────────────────────────────────────
    if server_proc is not None:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_proc.kill()
            server_proc.wait()

    # ── Restore keyring secrets ───────────────────────────────────────────────
    if _bak_api_key is not None:
        set_secret(KEYRING_API_KEY, _bak_api_key)
    else:
        delete_secret(KEYRING_API_KEY)

    if _bak_proxy_url is not None:
        set_secret(KEYRING_PROXY_URL, _bak_proxy_url)
    else:
        delete_secret(KEYRING_PROXY_URL)

    # ── Restore DB ────────────────────────────────────────────────────────────
    time.sleep(0.3)  # allow server process to release file handles
    if _db_existed:
        try:
            with sqlite3.connect(_db_path) as _con:
                _con.execute("DELETE FROM settings")
                if _bak_settings:
                    _con.executemany(
                        "INSERT INTO settings VALUES (?,?)", _bak_settings
                    )
                _con.commit()
            print("\n  [info] Isolated=No  -- real app.db settings table restored.")
        except Exception as e:
            print(f"\n  [warn] Could not restore settings rows: {e}")
    else:
        for ext in ("", "-wal", "-shm"):
            p = _db_path + ext
            if os.path.exists(p):
                try:
                    os.remove(p)
                except PermissionError:
                    pass
        print("\n  [info] Isolated=Yes -- test-only app.db removed.")

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
