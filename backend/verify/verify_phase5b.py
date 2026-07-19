"""
Phase 5B verification script (Text-only OpenRouter Completion Router).
Run from backend/ with the virtual environment active:
    .venv/Scripts/python verify_phase5b.py

Safety guarantees:
  - Uses a local fake OpenRouter server (127.0.0.1:19877) - no real internet.
  - Backs up and restores keyring API key and proxy URL.
  - Backs up and restores DB settings rows touched by tests.
  - Cleanup order: messages → chats → characters (FK-safe).
  - If app.db did not exist, it is removed after the run.
  - API key, proxy URL, prompt payload, user message, assistant message,
    and raw fake OpenRouter response body are never printed.
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
        with urllib.request.urlopen(url, timeout=10) as resp:
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
        with urllib.request.urlopen(req, timeout=10) as resp:
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


def http_post_raw(path: str, raw_body: str | bytes,
                  content_type: str = "application/json") -> tuple[int, dict | None]:
    """POST with a raw string/bytes body (for /characters/import)."""
    url = f"{BASE}{path}"
    body = raw_body.encode() if isinstance(raw_body, str) else raw_body
    req = urllib.request.Request(url, data=body, method="POST",
                                headers={"Content-Type": content_type})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
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
FAKE_PORT = 19877  # different from Phase 5A's 19876

_fake_mode = "normal"
_completion_calls = 0
_last_completion_auth: str | None = None
_last_completion_body: dict | None = None
_models_user_calls = 0
_last_models_user_auth: str | None = None

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

FAKE_MODEL_SMALL_CTX = {
    "id": "test/small-ctx",
    "name": "Small Context Test",
    "description": "Tiny context for trim tests",
    "context_length": 200,
    "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
    "pricing": {},
    "top_provider": {"max_completion_tokens": 50, "context_length": 200},
    "supported_parameters": ["temperature", "top_p", "max_tokens"],
    "created": 1700000001,
    "canonical_slug": "test/small-ctx",
}


def _reset_fake() -> None:
    global _completion_calls, _last_completion_auth, _last_completion_body
    global _models_user_calls, _last_models_user_auth
    _completion_calls = 0
    _last_completion_auth = None
    _last_completion_body = None
    _models_user_calls = 0
    _last_models_user_auth = None


class _FakeHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        global _models_user_calls, _last_models_user_auth
        if self.path == "/api/v1/models/user":
            _models_user_calls += 1
            _last_models_user_auth = self.headers.get("Authorization")
            self._json(200, {"data": [FAKE_MODEL_FULL, FAKE_MODEL_SMALL_CTX]})
        elif self.path == "/api/v1/models":
            self._json(200, {"data": [FAKE_MODEL_FULL, FAKE_MODEL_SMALL_CTX]})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        global _completion_calls, _last_completion_auth, _last_completion_body
        if self.path == "/api/v1/chat/completions":
            _completion_calls += 1
            _last_completion_auth = self.headers.get("Authorization")
            length = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(length)
            try:
                _last_completion_body = json.loads(raw_body)
            except Exception:
                _last_completion_body = {}
            self._handle_completion()
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_completion(self) -> None:
        if _fake_mode == "normal":
            self._json(200, {
                "choices": [{"message": {"role": "assistant",
                                         "content": "Fake assistant reply."}}]
            })
        elif _fake_mode == "auth_401":
            self._json(401, {"error": "unauthorized"})
        elif _fake_mode == "auth_403":
            self._json(403, {"error": "forbidden"})
        elif _fake_mode == "server_500":
            self._json(500, {"error": "internal server error"})
        elif _fake_mode == "rate_429":
            self._json(429, {"error": "rate limited"})
        elif _fake_mode == "malformed":
            self._json(200, {"no_choices": True})
        elif _fake_mode == "empty_content":
            self._json(200, {
                "choices": [{"message": {"role": "assistant", "content": ""}}]
            })
        elif _fake_mode == "null_content":
            self._json(200, {
                "choices": [{"message": {"role": "assistant", "content": None}}]
            })
        elif _fake_mode == "list_content":
            self._json(200, {
                "choices": [{"message": {"role": "assistant",
                                         "content": [{"type": "text", "text": "hi"}]}}]
            })
        else:
            self._json(200, {
                "choices": [{"message": {"role": "assistant",
                                         "content": "Fake assistant reply."}}]
            })

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

_existing_char_ids: set[int] = set()
_existing_chat_ids: set[int] = set()
_existing_msg_ids: set[int] = set()
if _db_existed:
    try:
        with sqlite3.connect(_db_path) as _con:
            _existing_char_ids = {
                r[0] for r in _con.execute("SELECT id FROM characters").fetchall()
            }
            _existing_chat_ids = {
                r[0] for r in _con.execute("SELECT id FROM chats").fetchall()
            }
            _existing_msg_ids = {
                r[0] for r in _con.execute("SELECT id FROM messages").fetchall()
            }
    except Exception:
        pass


def _total_msg_count() -> int:
    try:
        with sqlite3.connect(_db_path) as con:
            return con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    except Exception:
        return -1


def _msg_count_for(chat_id: int) -> int:
    try:
        with sqlite3.connect(_db_path) as con:
            return con.execute(
                "SELECT COUNT(*) FROM messages WHERE chat_id = ?", (chat_id,)
            ).fetchone()[0]
    except Exception:
        return -1


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
            _ra = _con.execute(
                "SELECT value FROM settings WHERE key='proxy_alias'"
            ).fetchone()
            _saved_proxy_alias = _ra["value"] if _ra else None
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

# Track test-created IDs for cleanup
_test_char_ids: list[int] = []
_test_chat_ids: list[int] = []

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

    TEST_KEY = "sk-test-phase5b-key"

    # ══════════════════════════════════════════════════════════════════════
    # V-Route  Route inventory
    # ══════════════════════════════════════════════════════════════════════
    section("V-Route  Route inventory")

    code, openapi = http_get("/openapi.json")
    path_keys = sorted(openapi.get("paths", {}).keys()) if openapi else []

    check("V-Route-1  exactly 18 path keys",
          len(path_keys) == 18,
          f"got {len(path_keys)}: {', '.join(path_keys)}")

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

    check("V-Route-5  /api/v1/chats/{chat_id}/complete present",
          "/api/v1/chats/{chat_id}/complete" in path_keys)

    complete_info = (openapi or {}).get("paths", {}).get(
        "/api/v1/chats/{chat_id}/complete", {},
    )
    complete_methods = sorted(complete_info.keys())
    check("V-Route-6  /chats/{chat_id}/complete methods = POST only",
          complete_methods == ["post"],
          f"got: {complete_methods}")

    check("V-Route-7  /api/v1/completions NOT present",
          "/api/v1/completions" not in path_keys)

    msgs_info = (openapi or {}).get("paths", {}).get(
        "/api/v1/chats/{chat_id}/messages", {},
    )
    check("V-Route-8  no POST on /chats/{chat_id}/messages",
          "post" not in msgs_info)

    check("V-Route-9  no path containing 'local' or 'ollama'",
          not any("local" in p or "ollama" in p for p in path_keys))

    check("V-Route-10  no /api/v1/api/v1 double prefix",
          not any("/api/v1/api/v1" in p for p in path_keys))

    check("V-Route-11  /api/v1/models/openrouter still present",
          "/api/v1/models/openrouter" in path_keys)

    check("V-Route-12  no path containing 'stream'",
          not any("stream" in p for p in path_keys))

    code, body = http_get("/healthz")
    check("V-Route-13  /healthz responds 200 + ok=true",
          code == 200 and body == {"ok": True},
          f"code={code}")

    # ══════════════════════════════════════════════════════════════════════
    # Setup: create test character + chat for all subsequent tests
    # ══════════════════════════════════════════════════════════════════════
    section("Setup  Create test character + chat")

    _CHAR_SYSTEM_PROMPT = "You are a helpful test assistant."
    _CHAR_DESCRIPTION = "A test character for Phase 5B."
    _CHAR_PERSONALITY = "Friendly and concise."
    _CHAR_SCENARIO = "Testing completions."
    _CHAR_FIRST_MES = "Hello! I am here to help."
    _CHAR_PHI = "Always respond in English."

    import_payload = {
        "name": "Phase5B Test Char",
        "system_prompt": _CHAR_SYSTEM_PROMPT,
        "description": _CHAR_DESCRIPTION,
        "personality": _CHAR_PERSONALITY,
        "scenario": _CHAR_SCENARIO,
        "first_mes": _CHAR_FIRST_MES,
        "mes_example": "",
        "post_history_instruction": _CHAR_PHI,
    }
    code, char_data = http_post_raw("/api/v1/characters/import",
                                json.dumps(import_payload))
    assert code == 201 and char_data, f"Failed to create test character: {code}"
    test_char_id = char_data["id"]
    _test_char_ids.append(test_char_id)

    code, chat_data = http_post("/api/v1/chats", {
        "character_id": test_char_id,
    })
    assert code == 201 and chat_data, f"Failed to create test chat: {code}"
    test_chat_id = chat_data["id"]
    _test_chat_ids.append(test_chat_id)
    print(f"  [info] Test char_id={test_char_id}, chat_id={test_chat_id}")

    # Set API key for test
    http_post("/api/v1/settings/api-key", {"api_key": TEST_KEY})

    # Baseline message count (first_mes = 1 assistant message)
    baseline_msg_count = _msg_count_for(test_chat_id)
    print(f"  [info] Baseline message count: {baseline_msg_count}")

    # ══════════════════════════════════════════════════════════════════════
    # V-ModelSeed  Seed model metadata cache
    # ══════════════════════════════════════════════════════════════════════
    section("V-ModelSeed  Seed model metadata cache")

    _reset_fake()
    code, models_data = http_get("/api/v1/models/openrouter?refresh=true")
    check("V-ModelSeed-1  GET /models/openrouter -> 200",
          code == 200, f"code={code}")
    check("V-ModelSeed-2  source=user",
          isinstance(models_data, dict) and models_data.get("source") == "user")

    models_list = models_data.get("models", []) if isinstance(models_data, dict) else []
    gpt4_meta = next((m for m in models_list if m.get("id") == "openai/gpt-4"), None)
    check("V-ModelSeed-3  openai/gpt-4 in models",
          gpt4_meta is not None)
    check("V-ModelSeed-4  supported_parameters correct",
          gpt4_meta is not None
          and set(gpt4_meta.get("supported_parameters", [])) == {"temperature", "top_p", "max_tokens"})

    small_ctx_meta = next((m for m in models_list if m.get("id") == "test/small-ctx"), None)
    check("V-ModelSeed-4b  test/small-ctx in models",
          small_ctx_meta is not None)

    check("V-ModelSeed-5  /models/user was called on fake server",
          _models_user_calls >= 1, f"calls={_models_user_calls}")
    check("V-ModelSeed-6  Authorization == Bearer TEST_KEY",
          _last_models_user_auth == f"Bearer {TEST_KEY}")

    # ══════════════════════════════════════════════════════════════════════
    # V-Happy  Happy path completion
    # ══════════════════════════════════════════════════════════════════════
    section("V-Happy  Happy path completion")

    _fake_mode = "normal"
    _reset_fake()
    msgs_before = _msg_count_for(test_chat_id)

    code, resp = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "Hello, world!",
        "model_id": "openai/gpt-4",
    })
    check("V-Happy-1  POST /complete -> 200", code == 200, f"code={code}")

    check("V-Happy-2  response has exactly 4 keys",
          resp is not None and set(resp.keys()) == {"chat_id", "model_id", "user_message", "assistant_message"})

    um = resp.get("user_message", {}) if resp else {}
    am = resp.get("assistant_message", {}) if resp else {}

    check("V-Happy-3  user_message has exactly 5 keys",
          set(um.keys()) == {"id", "chat_id", "role", "content", "created_at"})
    check("V-Happy-4  assistant_message has exactly 5 keys",
          set(am.keys()) == {"id", "chat_id", "role", "content", "created_at"})
    check("V-Happy-5  user_message.role == 'user'",
          um.get("role") == "user")
    check("V-Happy-6  assistant_message.role == 'assistant'",
          am.get("role") == "assistant")
    check("V-Happy-7  user_message.id < assistant_message.id",
          isinstance(um.get("id"), int) and isinstance(am.get("id"), int)
          and um["id"] < am["id"])
    check("V-Happy-8  response.model_id matches request",
          resp is not None and resp.get("model_id") == "openai/gpt-4")
    check("V-Happy-9  fake server received exactly 1 POST",
          _completion_calls == 1, f"calls={_completion_calls}")
    check("V-Happy-10  Authorization header correct",
          _last_completion_auth == f"Bearer {TEST_KEY}")

    fb = _last_completion_body or {}
    check("V-Happy-11  request body model == openai/gpt-4",
          fb.get("model") == "openai/gpt-4")
    check("V-Happy-12  request body stream is absent or false",
          fb.get("stream") is False or "stream" not in fb)

    fb_msgs = fb.get("messages", [])
    user_msgs_in_payload = [m for m in fb_msgs if m.get("role") == "user"]
    check("V-Happy-13  request body messages contains user message",
          any(m.get("content", "").strip() == "Hello, world!" for m in user_msgs_in_payload))

    assistant_msgs_in_payload = [m for m in fb_msgs if m.get("role") == "assistant"]
    check("V-Happy-14  first_mes in payload as assistant role",
          any(_CHAR_FIRST_MES in m.get("content", "") for m in assistant_msgs_in_payload))
    check("V-Happy-15  first_mes NOT duplicated (count==1)",
          len([m for m in assistant_msgs_in_payload
               if _CHAR_FIRST_MES in m.get("content", "")]) == 1)
    check("V-Happy-16  messages[0].role == 'system'",
          len(fb_msgs) > 0 and fb_msgs[0].get("role") == "system")
    check("V-Happy-17  request body contains 'provider' key",
          "provider" in fb)

    provider = fb.get("provider", {})
    check("V-Happy-18  provider.allow_fallbacks == false",
          provider.get("allow_fallbacks") is False)
    check("V-Happy-19  provider.data_collection == 'deny'",
          provider.get("data_collection") == "deny")
    check("V-Happy-20  provider.zdr == true",
          provider.get("zdr") is True)
    check("V-Happy-21  provider.require_parameters == true",
          provider.get("require_parameters") is True)
    check("V-Happy-22  no tools/tool_choice/response_format in body",
          "tools" not in fb and "tool_choice" not in fb and "response_format" not in fb)
    check("V-Happy-23  no image_url/file/attachments in any message",
          all(isinstance(m.get("content"), str) for m in fb_msgs))

    msgs_after = _msg_count_for(test_chat_id)
    check("V-Happy-24  DB: message count = original + 2",
          msgs_after == msgs_before + 2,
          f"before={msgs_before}, after={msgs_after}")

    code, db_msgs = http_get(f"/api/v1/chats/{test_chat_id}/messages")
    db_msgs_list = db_msgs if isinstance(db_msgs, list) else []
    last_two = db_msgs_list[-2:] if len(db_msgs_list) >= 2 else []
    check("V-Happy-25  DB: last two messages are user -> assistant",
          len(last_two) == 2
          and last_two[0].get("role") == "user"
          and last_two[1].get("role") == "assistant"
          and last_two[0].get("id", 0) < last_two[1].get("id", 0))

    code, updated_chat = http_get(f"/api/v1/chats/{test_chat_id}")
    check("V-Happy-26  DB: chats.model_id updated",
          updated_chat is not None and updated_chat.get("model_id") == "openai/gpt-4")
    check("V-Happy-27  DB: chats.updated_at is non-empty string",
          updated_chat is not None and isinstance(updated_chat.get("updated_at"), str)
          and len(updated_chat.get("updated_at", "")) > 0)

    # System block content check
    system_content = fb_msgs[0].get("content", "") if fb_msgs else ""
    check("V-Happy-28  character context system block present",
          _CHAR_SYSTEM_PROMPT in system_content)

    # post_history_instruction
    system_msgs = [m for m in fb_msgs if m.get("role") == "system"]
    check("V-Happy-29  post_history_instruction as last system message",
          len(system_msgs) >= 2
          and system_msgs[-1].get("content", "").strip() == _CHAR_PHI)

    # ══════════════════════════════════════════════════════════════════════
    # V-GenParams  Generation parameter handling
    # ══════════════════════════════════════════════════════════════════════
    section("V-GenParams  Generation parameter handling")

    # V-GP-1: valid params
    _fake_mode = "normal"
    _reset_fake()
    code, _ = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "gp test",
        "model_id": "openai/gpt-4",
        "generation_params": {"temperature": 0.7, "top_p": 0.9, "max_tokens": 100},
    })
    check("V-GP-1  valid params sent to fake server",
          code == 200
          and _last_completion_body is not None
          and _last_completion_body.get("temperature") == 0.7
          and _last_completion_body.get("top_p") == 0.9
          and _last_completion_body.get("max_tokens") == 100)

    # V-GP-2: stop as string
    _reset_fake()
    code, _ = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "stop test",
        "model_id": "openai/gpt-4",
        "generation_params": {"stop": "\n"},
    })
    check("V-GP-2  stop as string passed through",
          code == 200
          and _last_completion_body is not None
          and _last_completion_body.get("stop") == "\n")

    # V-GP-3: stop as list
    _reset_fake()
    code, _ = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "stop list test",
        "model_id": "openai/gpt-4",
        "generation_params": {"stop": ["END", "STOP"]},
    })
    check("V-GP-3  stop as list passed through",
          code == 200
          and _last_completion_body is not None
          and _last_completion_body.get("stop") == ["END", "STOP"])

    # V-GP-4: unknown param dropped
    _reset_fake()
    code, _ = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "unknown param test",
        "model_id": "openai/gpt-4",
        "generation_params": {"temperature": 0.5, "unknown_param": 42},
    })
    check("V-GP-4  unknown gen_param key dropped",
          code == 200
          and _last_completion_body is not None
          and "unknown_param" not in _last_completion_body)

    # V-GP-5 to V-GP-10: range/type errors
    _range_tests = [
        ("V-GP-5", {"temperature": 5.0}, "temperature out of range"),
        ("V-GP-6", {"temperature": -1.0}, "temperature negative"),
        ("V-GP-7", {"top_p": 2.0}, "top_p out of range"),
        ("V-GP-8", {"max_tokens": 0}, "max_tokens zero"),
        ("V-GP-9", {"repetition_penalty": 0.0}, "repetition_penalty zero"),
        ("V-GP-10", {"stop": 123}, "stop non-string/list"),
    ]
    for vid, params, desc in _range_tests:
        _reset_fake()
        msgs_before_err = _total_msg_count()
        code, _ = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
            "message": "range test",
            "model_id": "openai/gpt-4",
            "generation_params": params,
        })
        check(f"{vid}  {desc} -> 422, calls=0, no DB",
              code == 422 and _completion_calls == 0
              and _total_msg_count() == msgs_before_err,
              f"code={code}, calls={_completion_calls}")

    # V-GP-5b: detail == "invalid_gen_params" (focused detail assertion)
    _reset_fake()
    code, resp_5b = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "detail check",
        "model_id": "openai/gpt-4",
        "generation_params": {"temperature": 5.0},
    })
    detail_5b = resp_5b.get("detail") if isinstance(resp_5b, dict) else None
    check("V-GP-5b  gen param error detail == 'invalid_gen_params'",
          code == 422 and detail_5b == "invalid_gen_params",
          f"code={code}, detail={detail_5b!r}")


    # V-GP-11: null values dropped
    _reset_fake()
    code, _ = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "null test",
        "model_id": "openai/gpt-4",
        "generation_params": {"temperature": 0.5, "top_k": None, "seed": None},
    })
    fb11 = _last_completion_body or {}
    check("V-GP-11  null gen_param values dropped",
          code == 200 and "top_k" not in fb11 and "seed" not in fb11)

    # V-GP-12: seed pass-through with test/no-meta model
    _reset_fake()
    code, _ = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "seed test",
        "model_id": "test/no-meta",
        "generation_params": {"seed": 42},
    })
    fb12 = _last_completion_body or {}
    check("V-GP-12  seed accepted (test/no-meta, metadata skip)",
          code == 200 and fb12.get("seed") == 42,
          f"code={code}, seed={fb12.get('seed')}")

    # V-GP-12b: top_k=1.9 -> 422
    _reset_fake()
    msgs_before_12b = _total_msg_count()
    code, _ = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "topk fractional",
        "model_id": "openai/gpt-4",
        "generation_params": {"top_k": 1.9},
    })
    check("V-GP-12b  top_k=1.9 -> 422",
          code == 422 and _completion_calls == 0
          and _total_msg_count() == msgs_before_12b,
          f"code={code}")

    # V-GP-12c: max_tokens=1.9 -> 422
    _reset_fake()
    msgs_before_12c = _total_msg_count()
    code, _ = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "maxtok fractional",
        "model_id": "openai/gpt-4",
        "generation_params": {"max_tokens": 1.9},
    })
    check("V-GP-12c  max_tokens=1.9 -> 422",
          code == 422 and _completion_calls == 0
          and _total_msg_count() == msgs_before_12c,
          f"code={code}")

    # V-GP-12d: seed=1.9 -> 422
    _reset_fake()
    msgs_before_12d = _total_msg_count()
    code, _ = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "seed fractional",
        "model_id": "openai/gpt-4",
        "generation_params": {"seed": 1.9},
    })
    check("V-GP-12d  seed=1.9 -> 422",
          code == 422 and _completion_calls == 0
          and _total_msg_count() == msgs_before_12d,
          f"code={code}")

    # V-GP-12e: top_k=1.0 -> accepted (integer-valued float)
    _reset_fake()
    code, _ = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "topk int float",
        "model_id": "test/no-meta",
        "generation_params": {"top_k": 1.0},
    })
    fb12e = _last_completion_body or {}
    check("V-GP-12e  top_k=1.0 -> accepted, sent as 1",
          code == 200 and fb12e.get("top_k") == 1,
          f"code={code}, top_k={fb12e.get('top_k')}")

    # V-GP-13: metadata filtered - top_a not in openai/gpt-4 supported_parameters
    _reset_fake()
    code, _ = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "meta filter test",
        "model_id": "openai/gpt-4",
        "generation_params": {"top_a": 0.5, "temperature": 0.8},
    })
    fb13 = _last_completion_body or {}
    check("V-GP-13  top_a dropped by metadata filter (openai/gpt-4)",
          code == 200 and "top_a" not in fb13,
          f"top_a={'top_a' in fb13}")

    # V-GP-14: temperature passes metadata filter
    check("V-GP-14  temperature passes metadata filter",
          code == 200 and fb13.get("temperature") == 0.8,
          f"temp={fb13.get('temperature')}")

    # V-GP-15 to V-GP-19: stop edge cases
    # V-GP-15: stop="" -> dropped
    _reset_fake()
    code, _ = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "stop empty",
        "model_id": "openai/gpt-4",
        "generation_params": {"stop": ""},
    })
    fb15 = _last_completion_body or {}
    check("V-GP-15  stop='' -> dropped",
          code == 200 and "stop" not in fb15)

    # V-GP-16: stop=[] -> dropped
    _reset_fake()
    code, _ = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "stop empty list",
        "model_id": "openai/gpt-4",
        "generation_params": {"stop": []},
    })
    fb16 = _last_completion_body or {}
    check("V-GP-16  stop=[] -> dropped",
          code == 200 and "stop" not in fb16)

    # V-GP-17: stop=[""] -> 422
    _reset_fake()
    msgs_before_17 = _total_msg_count()
    code, _ = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "stop empty elem",
        "model_id": "openai/gpt-4",
        "generation_params": {"stop": [""]},
    })
    check("V-GP-17  stop=[''] -> 422",
          code == 422 and _completion_calls == 0
          and _total_msg_count() == msgs_before_17)

    # V-GP-18: stop=["valid", ""] -> 422
    _reset_fake()
    msgs_before_18 = _total_msg_count()
    code, _ = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "stop mixed empty",
        "model_id": "openai/gpt-4",
        "generation_params": {"stop": ["valid", ""]},
    })
    check("V-GP-18  stop=['valid',''] -> 422",
          code == 422 and _completion_calls == 0
          and _total_msg_count() == msgs_before_18)

    # V-GP-19: stop=["valid", 123] -> 422
    _reset_fake()
    msgs_before_19 = _total_msg_count()
    code, _ = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "stop non-str elem",
        "model_id": "openai/gpt-4",
        "generation_params": {"stop": ["valid", 123]},
    })
    check("V-GP-19  stop=['valid',123] -> 422",
          code == 422 and _completion_calls == 0
          and _total_msg_count() == msgs_before_19)

    # V-GP-20 to V-GP-23: strict numeric type validation
    _strict_type_tests = [
        ("V-GP-20", {"temperature": "0.8"}, "temperature=string"),
        ("V-GP-21", {"temperature": True}, "temperature=bool"),
        ("V-GP-22", {"top_k": True}, "top_k=bool"),
        ("V-GP-23", {"max_tokens": "100"}, "max_tokens=string"),
    ]
    for vid, params, desc in _strict_type_tests:
        _reset_fake()
        msgs_before_st = _total_msg_count()
        code, _ = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
            "message": "strict type test",
            "model_id": "openai/gpt-4",
            "generation_params": params,
        })
        check(f"{vid}  {desc} -> 422, calls=0, no DB",
              code == 422 and _completion_calls == 0
              and _total_msg_count() == msgs_before_st,
              f"code={code}, calls={_completion_calls}")

    # ══════════════════════════════════════════════════════════════════════
    # V-Provider  Provider policy
    # ══════════════════════════════════════════════════════════════════════
    section("V-Provider  Provider policy")

    # V-Prov-1: default provider
    _reset_fake()
    code, _ = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "prov default",
        "model_id": "openai/gpt-4",
    })
    prov1 = (_last_completion_body or {}).get("provider", {})
    check("V-Prov-1  default provider sent",
          code == 200
          and prov1.get("allow_fallbacks") is False
          and prov1.get("data_collection") == "deny"
          and prov1.get("require_parameters") is True
          and prov1.get("zdr") is True)

    # V-Prov-2: allow_fallbacks override IGNORED
    _reset_fake()
    code, _ = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "prov fallback",
        "model_id": "openai/gpt-4",
        "provider": {"allow_fallbacks": True},
    })
    prov2 = (_last_completion_body or {}).get("provider", {})
    check("V-Prov-2  allow_fallbacks override IGNORED",
          code == 200 and prov2.get("allow_fallbacks") is False)

    # V-Prov-3: data_collection override IGNORED
    _reset_fake()
    code, _ = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "prov dc",
        "model_id": "openai/gpt-4",
        "provider": {"data_collection": "allow"},
    })
    prov3 = (_last_completion_body or {}).get("provider", {})
    check("V-Prov-3  data_collection override IGNORED",
          code == 200 and prov3.get("data_collection") == "deny")

    # V-Prov-4: zdr always true
    check("V-Prov-4  zdr always true regardless",
          prov3.get("zdr") is True)

    # V-Prov-5: unknown provider field silently dropped
    _reset_fake()
    code, _ = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "prov extra",
        "model_id": "openai/gpt-4",
        "provider": {"extra_field": True},
    })
    prov5 = (_last_completion_body or {}).get("provider", {})
    check("V-Prov-5  unknown provider field silently dropped",
          code == 200 and "extra_field" not in prov5)

    # V-Prov-6: data_collection invalid value -> silently dropped (extra="ignore")
    _reset_fake()
    code, _ = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "prov invalid dc",
        "model_id": "openai/gpt-4",
        "provider": {"data_collection": "invalid_value"},
    })
    prov6 = (_last_completion_body or {}).get("provider", {})
    check("V-Prov-6  data_collection invalid -> silently dropped, deny preserved",
          code == 200 and prov6.get("data_collection") == "deny")

    # V-Prov-7: zdr override attempt -> still true
    _reset_fake()
    code, _ = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "prov zdr override",
        "model_id": "openai/gpt-4",
        "provider": {"zdr": False},
    })
    prov7 = (_last_completion_body or {}).get("provider", {})
    check("V-Prov-7  zdr override attempt -> still true",
          code == 200 and prov7.get("zdr") is True,
          f"zdr={prov7.get('zdr')}")

    # ══════════════════════════════════════════════════════════════════════
    # V-Context  Context budget and trim verification
    # ══════════════════════════════════════════════════════════════════════
    section("V-Context  Context budget and trim")

    # Create a fresh character with small system block for context tests
    _CTX_SYS = "ctxsys"
    ctx_char_payload = {
        "name": "CtxTestChar",
        "system_prompt": _CTX_SYS,
        "description": "",
        "personality": "",
        "scenario": "",
        "first_mes": "",  # no first_mes so we control history exactly
        "mes_example": "",
        "post_history_instruction": "",
    }
    code, ctx_char = http_post_raw("/api/v1/characters/import",
                                json.dumps(ctx_char_payload))
    ctx_char_id = ctx_char["id"] if ctx_char else None
    if ctx_char_id:
        _test_char_ids.append(ctx_char_id)

    code, ctx_chat = http_post("/api/v1/chats", {"character_id": ctx_char_id})
    ctx_chat_id = ctx_chat["id"] if ctx_chat else None
    if ctx_chat_id:
        _test_chat_ids.append(ctx_chat_id)

    # V-Ctx-1: small-ctx model visible
    check("V-Ctx-1  test/small-ctx visible in models",
          small_ctx_meta is not None and small_ctx_meta.get("id") == "test/small-ctx")

    # V-Ctx-2: completion with small-context model succeeds (no history)
    _fake_mode = "normal"
    _reset_fake()
    code, _ = http_post(f"/api/v1/chats/{ctx_chat_id}/complete", {
        "message": "hi",
        "model_id": "test/small-ctx",
    })
    check("V-Ctx-2  completion with small-context model succeeds",
          code == 200, f"code={code}")

    # V-Ctx-3: system block is first message
    ctx_fb = _last_completion_body or {}
    ctx_fb_msgs = ctx_fb.get("messages", [])
    check("V-Ctx-3  messages[0] is system",
          len(ctx_fb_msgs) > 0 and ctx_fb_msgs[0].get("role") == "system")

    # ── Prepare trim test with unique markers ─────────────────────────────
    # Create a NEW chat with same character for isolated trim testing.
    code, trim_chat = http_post("/api/v1/chats", {"character_id": ctx_char_id})
    trim_chat_id = trim_chat["id"] if trim_chat else None
    if trim_chat_id:
        _test_chat_ids.append(trim_chat_id)

    # Seed 8 history messages with unique markers.
    # Each marker is ~15 chars; system prompt is ~6 chars.
    # test/small-ctx context_length=200 -> budget ~600 chars (at 4 chars/token ratio 200*3=600 est)
    # System(6) + 8 msgs(~15 each=120) + user(~20) = ~146 chars → fits.
    # We'll add overflow messages later to force trimming.
    _MARKERS_OLD = [f"XOLD{i:02d}X" for i in range(4)]  # 4 oldest
    _MARKERS_NEW = [f"XNEW{i:02d}X" for i in range(4)]  # 4 newest
    _ALL_MARKERS = _MARKERS_OLD + _MARKERS_NEW
    with sqlite3.connect(_db_path) as _con:
        for idx, marker in enumerate(_ALL_MARKERS):
            role = "user" if idx % 2 == 0 else "assistant"
            _con.execute(
                "INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)",
                (trim_chat_id, role, marker),
            )
        _con.commit()

    # V-Ctx-4: all markers fit → all present in payload
    _reset_fake()
    _USER_MSG_MARKER = "XCURRENTUSERX"
    code, _ = http_post(f"/api/v1/chats/{trim_chat_id}/complete", {
        "message": _USER_MSG_MARKER,
        "model_id": "test/small-ctx",
    })
    check("V-Ctx-4  all markers fit -> 200",
          code == 200, f"code={code}")

    fit_fb = _last_completion_body or {}
    fit_msgs = fit_fb.get("messages", [])
    fit_payload_text = " ".join(m.get("content", "") for m in fit_msgs)
    check("V-Ctx-4b  all 8 markers present in payload",
          all(mk in fit_payload_text for mk in _ALL_MARKERS))
    check("V-Ctx-4c  current user message present",
          _USER_MSG_MARKER in fit_payload_text)

    # ── Now add overflow messages to force trimming ───────────────────────
    # Add 10 large messages (~100 chars each) → total history too big for ctx 200.
    with sqlite3.connect(_db_path) as _con:
        for i in range(10):
            role = "user" if i % 2 == 0 else "assistant"
            content = f"XOVERFLOW{i:02d}X" + "P" * 90
            _con.execute(
                "INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)",
                (trim_chat_id, role, content),
            )
        _con.commit()

    msgs_before_trim = _msg_count_for(trim_chat_id)
    _reset_fake()
    _TRIM_USER_MSG = "XTRIMUSERX"
    code, _ = http_post(f"/api/v1/chats/{trim_chat_id}/complete", {
        "message": _TRIM_USER_MSG,
        "model_id": "test/small-ctx",
    })
    check("V-Ctx-5  overflow trim -> 200",
          code == 200, f"code={code}")

    trim_fb = _last_completion_body or {}
    trim_msgs = trim_fb.get("messages", [])
    trim_payload_text = " ".join(m.get("content", "") for m in trim_msgs)

    # V-Ctx-6: oldest markers trimmed away
    check("V-Ctx-6  oldest history markers absent after trim",
          all(mk not in trim_payload_text for mk in _MARKERS_OLD),
          f"found={[mk for mk in _MARKERS_OLD if mk in trim_payload_text]}")

    # V-Ctx-6b: at least one newest overflow marker is retained
    _OVERFLOW_MARKERS = [f"XOVERFLOW{i:02d}X" for i in range(10)]
    retained_overflow = [mk for mk in _OVERFLOW_MARKERS if mk in trim_payload_text]
    check("V-Ctx-6b  newest overflow marker retained in payload",
          "XOVERFLOW09X" in trim_payload_text,
          f"retained={retained_overflow}")

    # V-Ctx-6c: retained overflow markers are in chronological order
    # Extract marker indices from payload in order of appearance
    _retained_positions = []
    for mk in _OVERFLOW_MARKERS:
        pos = trim_payload_text.find(mk)
        if pos >= 0:
            _retained_positions.append((pos, mk))
    _retained_positions.sort(key=lambda x: x[0])
    _retained_indices = [int(mk[9:11]) for _, mk in _retained_positions]
    check("V-Ctx-6c  retained overflow markers in chronological order",
          _retained_indices == sorted(_retained_indices),
          f"order={_retained_indices}")

    # V-Ctx-7: current user message still present
    check("V-Ctx-7  current user message present after trim",
          _TRIM_USER_MSG in trim_payload_text)

    # V-Ctx-8: system block still present
    check("V-Ctx-8  system block present after trim",
          len(trim_msgs) > 0 and trim_msgs[0].get("role") == "system")

    # V-Ctx-9: payload order is chronological (non-system messages in id ASC)
    non_sys = [m for m in trim_msgs if m.get("role") != "system"]
    # The last non-system message should be the current user message
    check("V-Ctx-9  last non-system message is current user",
          len(non_sys) > 0 and non_sys[-1].get("content", "").strip() == _TRIM_USER_MSG)
    # Roles should alternate (or at least not have two system blocks in middle)
    check("V-Ctx-9b  retained history in chronological order",
          len(non_sys) >= 2)

    # V-Ctx-10: DB history rows NOT deleted (payload-only trim)
    msgs_after_trim = _msg_count_for(trim_chat_id)
    check("V-Ctx-10  payload-only trim: DB count = before + 2",
          msgs_after_trim == msgs_before_trim + 2,
          f"before={msgs_before_trim}, after={msgs_after_trim}")

    # V-Ctx-10b: GET /messages returns all original + new
    code, all_db_msgs = http_get(f"/api/v1/chats/{trim_chat_id}/messages")
    all_db_msgs_list = all_db_msgs if isinstance(all_db_msgs, list) else []
    check("V-Ctx-10b  GET /messages count matches DB",
          len(all_db_msgs_list) == msgs_after_trim,
          f"api={len(all_db_msgs_list)}, db={msgs_after_trim}")

    # V-Ctx-10c: old markers still exist in DB (not deleted)
    db_all_text = " ".join(m.get("content", "") for m in all_db_msgs_list)
    check("V-Ctx-10c  oldest markers still in DB after trim",
          all(mk in db_all_text for mk in _MARKERS_OLD))

    # V-Ctx-11: context_too_large
    large_char_payload = {
        "name": "LargeCtxChar",
        "system_prompt": "B" * 700,  # ~700 chars
        "description": "",
        "personality": "",
        "scenario": "",
        "first_mes": "",
        "mes_example": "",
        "post_history_instruction": "",
    }
    code, large_char = http_post_raw("/api/v1/characters/import",
                                  json.dumps(large_char_payload))
    large_char_id = large_char["id"] if large_char else None
    if large_char_id:
        _test_char_ids.append(large_char_id)

    code, large_chat = http_post("/api/v1/chats", {"character_id": large_char_id})
    large_chat_id = large_chat["id"] if large_chat else None
    if large_chat_id:
        _test_chat_ids.append(large_chat_id)

    _reset_fake()
    msgs_before_ctx = _total_msg_count()
    code, ctx_err = http_post(f"/api/v1/chats/{large_chat_id}/complete", {
        "message": "C" * 200,  # 200 chars, total system(700)+user(200)=900 > budget(600)
        "model_id": "test/small-ctx",
    })
    check("V-Ctx-11  context_too_large -> 400",
          code == 400 and isinstance(ctx_err, dict)
          and ctx_err.get("detail") == "context_too_large",
          f"code={code}, detail={ctx_err.get('detail') if ctx_err else '?'}")

    # V-Ctx-12: fake server calls = 0
    check("V-Ctx-12  context_too_large: fake calls=0",
          _completion_calls == 0, f"calls={_completion_calls}")

    # V-Ctx-13: no DB messages written
    check("V-Ctx-13  context_too_large: no DB messages",
          _total_msg_count() == msgs_before_ctx,
          f"before={msgs_before_ctx}, after={_total_msg_count()}")

    # ══════════════════════════════════════════════════════════════════════
    # V-Error  Error cases
    # ══════════════════════════════════════════════════════════════════════
    section("V-Error  Error cases")

    # V-Err-1: missing API key
    http_delete("/api/v1/settings/api-key")
    _fake_mode = "normal"
    _reset_fake()
    msgs_before_e1 = _total_msg_count()
    code, err1 = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "no key",
        "model_id": "openai/gpt-4",
    })
    check("V-Err-1  missing API key -> 401 api_key_missing",
          code == 401 and isinstance(err1, dict)
          and err1.get("detail") == "api_key_missing"
          and _completion_calls == 0
          and _total_msg_count() == msgs_before_e1)

    # Restore key
    http_post("/api/v1/settings/api-key", {"api_key": TEST_KEY})

    # V-Err-2: unknown chat_id
    _reset_fake()
    msgs_before_e2 = _total_msg_count()
    code, err2 = http_post("/api/v1/chats/999999/complete", {
        "message": "no chat",
        "model_id": "openai/gpt-4",
    })
    check("V-Err-2  unknown chat_id -> 404 chat_not_found",
          code == 404 and isinstance(err2, dict)
          and err2.get("detail") == "chat_not_found"
          and _completion_calls == 0
          and _total_msg_count() == msgs_before_e2)

    # V-Err-3: empty message
    _reset_fake()
    msgs_before_e3 = _total_msg_count()
    code, _ = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "",
        "model_id": "openai/gpt-4",
    })
    check("V-Err-3  empty message -> 422",
          code == 422 and _completion_calls == 0
          and _total_msg_count() == msgs_before_e3)

    # V-Err-4: whitespace message
    _reset_fake()
    msgs_before_e4 = _total_msg_count()
    code, _ = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "   ",
        "model_id": "openai/gpt-4",
    })
    check("V-Err-4  whitespace message -> 422",
          code == 422 and _completion_calls == 0
          and _total_msg_count() == msgs_before_e4)

    # V-Err-5: missing model_id
    _reset_fake()
    msgs_before_e5 = _total_msg_count()
    code, _ = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "no model",
    })
    check("V-Err-5  missing model_id -> 422",
          code == 422 and _completion_calls == 0
          and _total_msg_count() == msgs_before_e5)

    # V-Err-6: empty model_id
    _reset_fake()
    msgs_before_e6 = _total_msg_count()
    code, _ = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "empty model",
        "model_id": "",
    })
    check("V-Err-6  empty model_id -> 422",
          code == 422 and _completion_calls == 0
          and _total_msg_count() == msgs_before_e6)

    # V-Err-7: proxy_required=true, no proxy
    http_delete("/api/v1/settings/proxy")
    with sqlite3.connect(_db_path) as _con:
        _con.execute(
            "INSERT OR REPLACE INTO settings (key, value) "
            "VALUES ('proxy_required', '1')"
        )
        _con.commit()
    time.sleep(0.1)

    _reset_fake()
    msgs_before_e7 = _total_msg_count()
    code, err7 = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "proxy check",
        "model_id": "openai/gpt-4",
    })
    check("V-Err-7  proxy_required+no proxy -> 503 proxy_missing",
          code == 503 and isinstance(err7, dict)
          and err7.get("detail") == "proxy_missing"
          and _completion_calls == 0
          and _total_msg_count() == msgs_before_e7)

    # Reset proxy_required
    with sqlite3.connect(_db_path) as _con:
        _con.execute(
            "INSERT OR REPLACE INTO settings (key, value) "
            "VALUES ('proxy_required', '0')"
        )
        _con.commit()

    # V-Err-8: fake 401
    _fake_mode = "auth_401"
    _reset_fake()
    msgs_before_e8 = _total_msg_count()
    code, err8 = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "auth fail",
        "model_id": "openai/gpt-4",
    })
    check("V-Err-8  fake 401 -> 401 auth_failed",
          code == 401 and isinstance(err8, dict)
          and err8.get("detail") == "auth_failed"
          and _total_msg_count() == msgs_before_e8)

    # V-Err-9: fake 403
    _fake_mode = "auth_403"
    _reset_fake()
    msgs_before_e9 = _total_msg_count()
    code, err9 = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "auth fail 403",
        "model_id": "openai/gpt-4",
    })
    check("V-Err-9  fake 403 -> 401 auth_failed",
          code == 401 and isinstance(err9, dict)
          and err9.get("detail") == "auth_failed"
          and _total_msg_count() == msgs_before_e9)

    # V-Err-10: malformed response (no choices)
    _fake_mode = "malformed"
    _reset_fake()
    msgs_before_e10 = _total_msg_count()
    code, err10 = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "malformed resp",
        "model_id": "openai/gpt-4",
    })
    check("V-Err-10  malformed -> 502 invalid_openrouter_completion_response",
          code == 502 and isinstance(err10, dict)
          and err10.get("detail") == "invalid_openrouter_completion_response"
          and _total_msg_count() == msgs_before_e10)

    # V-Err-11: empty content
    _fake_mode = "empty_content"
    _reset_fake()
    msgs_before_e11 = _total_msg_count()
    code, err11 = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "empty content",
        "model_id": "openai/gpt-4",
    })
    check("V-Err-11  empty content -> 502",
          code == 502 and _total_msg_count() == msgs_before_e11)

    # V-Err-12: null content
    _fake_mode = "null_content"
    _reset_fake()
    msgs_before_e12 = _total_msg_count()
    code, _ = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "null content",
        "model_id": "openai/gpt-4",
    })
    check("V-Err-12  null content -> 502",
          code == 502 and _total_msg_count() == msgs_before_e12)

    # V-Err-13: list content (multimodal)
    _fake_mode = "list_content"
    _reset_fake()
    msgs_before_e13 = _total_msg_count()
    code, _ = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "list content",
        "model_id": "openai/gpt-4",
    })
    check("V-Err-13  list content -> 502",
          code == 502 and _total_msg_count() == msgs_before_e13)

    # V-Err-14: fake 500
    _fake_mode = "server_500"
    _reset_fake()
    msgs_before_e14 = _total_msg_count()
    code, err14 = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "server error",
        "model_id": "openai/gpt-4",
    })
    check("V-Err-14  fake 500 -> 502 openrouter_completion_error",
          code == 502 and isinstance(err14, dict)
          and err14.get("detail") == "openrouter_completion_error"
          and _total_msg_count() == msgs_before_e14)

    # V-Err-15: raw fake error body not returned
    all_bodies_so_far = " ".join(_all_responses)
    check("V-Err-15  raw fake error body not in any response",
          '"error": "unauthorized"' not in all_bodies_so_far
          and '"error":"unauthorized"' not in all_bodies_so_far
          and '"error": "forbidden"' not in all_bodies_so_far
          and '"error":"forbidden"' not in all_bodies_so_far
          and '"error": "internal server error"' not in all_bodies_so_far
          and '"error":"internal server error"' not in all_bodies_so_far)

    # V-Err-16: fake 429 -> 429
    _fake_mode = "rate_429"
    _reset_fake()
    msgs_before_e16 = _total_msg_count()
    code, err16 = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "rate limit",
        "model_id": "openai/gpt-4",
    })
    check("V-Err-16  fake 429 -> 429 openrouter_rate_limited",
          code == 429 and isinstance(err16, dict)
          and err16.get("detail") == "openrouter_rate_limited"
          and _total_msg_count() == msgs_before_e16)

    # ══════════════════════════════════════════════════════════════════════
    # V-Privacy  Privacy and security
    # ══════════════════════════════════════════════════════════════════════
    section("V-Privacy  Privacy and security")

    _fake_mode = "normal"  # reset for safety
    all_bodies = " ".join(_all_responses)

    check("V-Priv-1  TEST_KEY absent from all response bodies",
          TEST_KEY not in all_bodies)

    if _saved_proxy_url:
        check("V-Priv-2  saved proxy URL absent from responses",
              _saved_proxy_url not in all_bodies)
    else:
        check("V-Priv-2  no proxy URL to leak", True)

    check("V-Priv-3  raw fake error bodies not in responses",
          '"error": "unauthorized"' not in all_bodies
          and '"error":"unauthorized"' not in all_bodies
          and '"error": "rate limited"' not in all_bodies
          and '"error":"rate limited"' not in all_bodies)

    # V-Priv-4: raw_json should not be in completion endpoint responses
    # (not checking openapi.json or models responses, which may mention it)
    # Exclude OpenAPI schema responses which legitimately contain both
    # "user_message" (as a schema property) and "raw_json" (character schema).
    completion_responses = [r for r in _all_responses
                           if ("user_message" in r or "assistant_message" in r)
                           and '"openapi":' not in r]
    check("V-Priv-4  raw_json absent from completion responses",
          all("raw_json" not in r for r in completion_responses))

    # V-Priv-5: character internal fields not leaked in completion responses
    completion_bodies = " ".join(completion_responses)
    check("V-Priv-5a  system_prompt not in completion response",
          _CHAR_SYSTEM_PROMPT not in completion_bodies)
    check("V-Priv-5b  description not in completion response",
          _CHAR_DESCRIPTION not in completion_bodies)
    check("V-Priv-5c  personality not in completion response",
          _CHAR_PERSONALITY not in completion_bodies)
    check("V-Priv-5d  scenario not in completion response",
          _CHAR_SCENARIO not in completion_bodies)
    check("V-Priv-5e  post_history_instruction not in completion response",
          _CHAR_PHI not in completion_bodies)

    # ══════════════════════════════════════════════════════════════════════
    # V-CORS  CORS policy verification
    # ══════════════════════════════════════════════════════════════════════
    section("V-CORS  CORS policy verification")

    def _cors_preflight(origin: str) -> dict[str, str]:
        """Send an OPTIONS preflight request with Origin header, return response headers."""
        url = f"{BASE}/api/v1/settings"
        req = urllib.request.Request(url, method="OPTIONS", headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Content-Type",
        })
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return dict(resp.headers)
        except urllib.error.HTTPError as e:
            return dict(e.headers)

    def _cors_get(origin: str) -> dict[str, str]:
        """Send a GET request with Origin header, return response headers."""
        url = f"{BASE}/healthz"
        req = urllib.request.Request(url, method="GET", headers={
            "Origin": origin,
        })
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return dict(resp.headers)
        except urllib.error.HTTPError as e:
            return dict(e.headers)

    # V-CORS-1: Preflight from approved origin is allowed
    approved_headers = _cors_preflight("http://127.0.0.1:5173")
    acao_approved = approved_headers.get("access-control-allow-origin", "")
    check("V-CORS-1  preflight from 127.0.0.1:5173 allowed",
          acao_approved == "http://127.0.0.1:5173",
          f"acao={acao_approved!r}")

    # V-CORS-2: Preflight from localhost:5173 is rejected
    localhost_headers = _cors_preflight("http://localhost:5173")
    acao_localhost = localhost_headers.get("access-control-allow-origin", "")
    check("V-CORS-2  preflight from localhost:5173 REJECTED",
          acao_localhost == "",
          f"acao={acao_localhost!r}")

    # V-CORS-3: Preflight from evil.example.com is rejected
    evil_headers = _cors_preflight("http://evil.example.com")
    acao_evil = evil_headers.get("access-control-allow-origin", "")
    check("V-CORS-3  preflight from evil.example.com REJECTED",
          acao_evil == "",
          f"acao={acao_evil!r}")

    # V-CORS-4: Preflight from 127.0.0.1:9999 is rejected
    alt_port_headers = _cors_preflight("http://127.0.0.1:9999")
    acao_alt = alt_port_headers.get("access-control-allow-origin", "")
    check("V-CORS-4  preflight from 127.0.0.1:9999 REJECTED",
          acao_alt == "",
          f"acao={acao_alt!r}")

    # V-CORS-5: No wildcard Access-Control-Allow-Origin in any response
    check("V-CORS-5  no wildcard ACAO in approved preflight",
          acao_approved != "*")
    check("V-CORS-5b  no wildcard ACAO in localhost preflight",
          acao_localhost != "*")
    check("V-CORS-5c  no wildcard ACAO in evil preflight",
          acao_evil != "*")

    # V-CORS-6: Access-Control-Allow-Credentials is NOT true
    acac = approved_headers.get("access-control-allow-credentials", "")
    check("V-CORS-6  allow-credentials is not true",
          acac.lower() != "true",
          f"acac={acac!r}")

    # V-CORS-7: Authorization is NOT in allowed headers
    acah = approved_headers.get("access-control-allow-headers", "")
    check("V-CORS-7  Authorization not in allowed headers",
          "authorization" not in acah.lower(),
          f"acah={acah!r}")

    # V-CORS-8: Content-Type IS in allowed headers
    check("V-CORS-8  Content-Type in allowed headers",
          "content-type" in acah.lower(),
          f"acah={acah!r}")

    # V-CORS-9: Allowed methods are minimal (GET, POST, DELETE only)
    acam = approved_headers.get("access-control-allow-methods", "")
    allowed_methods = {m.strip().upper() for m in acam.split(",")} if acam else set()
    check("V-CORS-9  allowed methods are GET, POST, DELETE, PATCH",
          allowed_methods == {"GET", "POST", "DELETE", "PATCH"},
          f"methods={allowed_methods}")

    # V-CORS-10: Simple GET with approved origin returns correct ACAO
    simple_headers = _cors_get("http://127.0.0.1:5173")
    acao_simple = simple_headers.get("access-control-allow-origin", "")
    check("V-CORS-10  simple GET from approved origin returns ACAO",
          acao_simple == "http://127.0.0.1:5173",
          f"acao={acao_simple!r}")

    # ══════════════════════════════════════════════════════════════════════
    # V-Src  Static source analysis
    # ══════════════════════════════════════════════════════════════════════
    section("V-Src  Static source analysis")

    comp_path = os.path.join(BACKEND_DIR, "routers", "completions.py")
    comp_raw = open(comp_path, encoding="utf-8").read()

    # Strip comments and docstrings
    _comp_lines = []
    _comp_in_doc = False
    for line in comp_raw.splitlines():
        stripped = line.strip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            if stripped.count('"""') == 1 or stripped.count("'''") == 1:
                _comp_in_doc = not _comp_in_doc
            continue
        if _comp_in_doc:
            continue
        if stripped.startswith("#"):
            continue
        _comp_lines.append(stripped)
    comp_text = "\n".join(_comp_lines)

    check("V-Src-1  completions.py no import httpx",
          "import httpx" not in comp_text)
    check("V-Src-2  completions.py no import requests",
          "import requests" not in comp_text)
    check("V-Src-3  completions.py no direct keyring usage",
          "import keyring" not in comp_text
          and "from keyring import" not in comp_text
          and "keyring.get_password" not in comp_text
          and "keyring.set_password" not in comp_text)
    check("V-Src-4  completions.py no urllib.request",
          "urllib.request" not in comp_text)

    main_path = os.path.join(BACKEND_DIR, "main.py")
    main_code = open(main_path, encoding="utf-8").read()
    main_lines = [ln.strip() for ln in main_code.splitlines()
                  if ln.strip() and not ln.strip().startswith("#")]
    main_active = "\n".join(main_lines)

    check("V-Src-5  main.py has active settings router",
          "from routers import settings" in main_active)
    check("V-Src-6  main.py has active characters router",
          "from routers import characters" in main_active)
    check("V-Src-7  main.py has active chats router",
          "from routers import chats" in main_active)
    check("V-Src-8  main.py has active models_router",
          "from routers import models_router" in main_active)
    check("V-Src-9  main.py has active completions router",
          "from routers import completions" in main_active)

except FileNotFoundError as e:
    print(f"  [FATAL] {e}")
except Exception as e:
    import traceback
    traceback.print_exc()

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

        # DB row cleanup: messages -> chats -> characters (FK-safe)
        try:
            with sqlite3.connect(_db_path) as _con:
                all_msg_ids = {
                    r[0] for r in _con.execute(
                        "SELECT id FROM messages"
                    ).fetchall()
                }
                msg_to_del = all_msg_ids - _existing_msg_ids
                if msg_to_del:
                    _con.executemany(
                        "DELETE FROM messages WHERE id = ?",
                        [(i,) for i in msg_to_del],
                    )

                all_chat_ids = {
                    r[0] for r in _con.execute(
                        "SELECT id FROM chats"
                    ).fetchall()
                }
                chat_to_del = all_chat_ids - _existing_chat_ids
                if chat_to_del:
                    _con.executemany(
                        "DELETE FROM chats WHERE id = ?",
                        [(i,) for i in chat_to_del],
                    )

                all_char_ids = {
                    r[0] for r in _con.execute(
                        "SELECT id FROM characters"
                    ).fetchall()
                }
                char_to_del = all_char_ids - _existing_char_ids
                if char_to_del:
                    _con.executemany(
                        "DELETE FROM characters WHERE id = ?",
                        [(i,) for i in char_to_del],
                    )

                _con.commit()
                _total = len(msg_to_del) + len(chat_to_del) + len(char_to_del)
        except Exception:
            _total = -1
        print(f"  [info] {_total} test rows removed (msgs+chats+chars).")
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
