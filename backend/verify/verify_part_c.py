"""
Part C verification - Persona Persistence + Injection.
Run from backend/ with the virtual environment active:
    .venv/Scripts/python verify_part_c.py
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

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

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


def http_get(path: str) -> tuple[int, dict | list | None]:
    url = f"{BASE}{path}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
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
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


def http_post_raw(path: str, raw_body: str | bytes,
                  content_type: str = "application/json") -> tuple[int, dict | None]:
    url = f"{BASE}{path}"
    body = raw_body.encode() if isinstance(raw_body, str) else raw_body
    req = urllib.request.Request(url, data=body, method="POST",
                                headers={"Content-Type": content_type})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


def http_patch(path: str, data: dict) -> tuple[int, dict | None]:
    url = f"{BASE}{path}"
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method="PATCH",
                                headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


def http_delete(path: str) -> tuple[int, dict | None]:
    url = f"{BASE}{path}"
    req = urllib.request.Request(url, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


# ── Fake OpenRouter server ────────────────────────────────────────────────────
FAKE_PORT = 19880
_last_completion_body: dict | None = None

FAKE_MODEL = {
    "id": "openai/gpt-4",
    "name": "GPT-4",
    "description": "Test",
    "context_length": 128000,
    "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
    "pricing": {"prompt": "0.00003", "completion": "0.00006"},
    "top_provider": {"max_completion_tokens": 4096, "context_length": 128000},
    "supported_parameters": ["temperature", "top_p", "max_tokens"],
    "created": 1700000000,
    "canonical_slug": "openai/gpt-4",
}


class _FakeHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path in ("/api/v1/models/user", "/api/v1/models"):
            self._json(200, {"data": [FAKE_MODEL]})
        elif self.path == "/api/v1/key":
            self._json(200, {"data": {"label": "test-key"}})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        global _last_completion_body
        if self.path == "/api/v1/chat/completions":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            try:
                _last_completion_body = json.loads(raw)
            except Exception:
                _last_completion_body = {}
            self._json(200, {
                "choices": [{"message": {"role": "assistant",
                                         "content": "Fake reply."}}]
            })
        else:
            self.send_response(404)
            self.end_headers()

    def _json(self, code: int, data: dict) -> None:
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args) -> None:
        pass


# ── DB + Keyring backup ──────────────────────────────────────────────────────
_db_path = os.path.join(BACKEND_DIR, "app.db")
_db_existed = os.path.exists(_db_path)

_existing_char_ids: set[int] = set()
_existing_chat_ids: set[int] = set()
_existing_msg_ids: set[int] = set()
_existing_persona_ids: set[int] = set()
if _db_existed:
    try:
        with sqlite3.connect(_db_path) as _con:
            _existing_char_ids = {r[0] for r in _con.execute("SELECT id FROM characters").fetchall()}
            _existing_chat_ids = {r[0] for r in _con.execute("SELECT id FROM chats").fetchall()}
            _existing_msg_ids = {r[0] for r in _con.execute("SELECT id FROM messages").fetchall()}
            try:
                _existing_persona_ids = {r[0] for r in _con.execute("SELECT id FROM personas").fetchall()}
            except Exception:
                pass
    except Exception:
        pass

import keyring
import keyring.errors

_KR_SERVICE = "chatbot_interface"
_KR_API_KEY = "openrouter_api_key"
_saved_api_key = keyring.get_password(_KR_SERVICE, _KR_API_KEY)

_uvicorn_name = "uvicorn.exe" if sys.platform == "win32" else "uvicorn"
UVICORN_EXE = os.path.join(os.path.dirname(sys.executable), _uvicorn_name)

server_proc: subprocess.Popen | None = None
fake_server: http.server.HTTPServer | None = None

try:
    urllib.request.urlopen(f"{BASE}/healthz", timeout=1)
    print("  [FATAL] port_8787_already_in_use")
    sys.exit(1)
except Exception:
    pass

try:
    fake_server = http.server.HTTPServer(("127.0.0.1", FAKE_PORT), _FakeHandler)
    fake_thread = threading.Thread(target=fake_server.serve_forever, daemon=True)
    fake_thread.start()

    env = os.environ.copy()
    env["OPENROUTER_BASE_URL"] = f"http://127.0.0.1:{FAKE_PORT}/api/v1"

    server_proc = subprocess.Popen(
        [UVICORN_EXE, "main:app", "--host", "127.0.0.1", "--port", "8787",
         "--log-level", "warning"],
        cwd=BACKEND_DIR, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    started = False
    for _ in range(30):
        time.sleep(0.2)
        try:
            urllib.request.urlopen(f"{BASE}/healthz", timeout=1)
            started = True
            break
        except Exception:
            pass
    assert started, "Backend did not start"

    # Setup
    TEST_KEY = "sk-test-part-c"
    http_post("/api/v1/settings/api-key", {"api_key": TEST_KEY})
    http_get("/api/v1/models/openrouter")

    # ══════════════════════════════════════════════════════════════════════
    # V-C-1: POST /personas → create
    # ══════════════════════════════════════════════════════════════════════
    section("V-C-1  POST /personas create")
    code, p1 = http_post("/api/v1/personas", {
        "display_name": "Test Persona Alpha",
        "description": "I always respond sarcastically.",
    })
    check("V-C-1  create persona → 201",
          code == 201 and p1.get("id") is not None,
          f"code={code}")
    p1_id = p1["id"] if p1 else None

    check("V-C-1b  has display_name, description, created_at, updated_at",
          all(k in (p1 or {}) for k in ("display_name", "description", "created_at", "updated_at")))

    # ══════════════════════════════════════════════════════════════════════
    # V-C-2: GET /personas → list
    # ══════════════════════════════════════════════════════════════════════
    section("V-C-2  GET /personas list")
    code, plist = http_get("/api/v1/personas")
    check("V-C-2  list → 200 + array",
          code == 200 and isinstance(plist, list),
          f"code={code}")
    check("V-C-2b  created persona in list",
          any(p.get("id") == p1_id for p in (plist or [])))

    # ══════════════════════════════════════════════════════════════════════
    # V-C-3: PATCH /personas/{id} → edit
    # ══════════════════════════════════════════════════════════════════════
    section("V-C-3  PATCH /personas/{id}")
    code, patched = http_patch(f"/api/v1/personas/{p1_id}", {
        "display_name": "Alpha Patched",
    })
    check("V-C-3  patch → 200 + updated name",
          code == 200 and patched.get("display_name") == "Alpha Patched",
          f"code={code} name={patched.get('display_name') if patched else None}")

    check("V-C-3b  description unchanged",
          patched.get("description") == "I always respond sarcastically.")

    # ══════════════════════════════════════════════════════════════════════
    # V-C-4: PATCH nonexistent persona → 404
    # ══════════════════════════════════════════════════════════════════════
    section("V-C-4  PATCH nonexistent persona")
    code, body = http_patch("/api/v1/personas/999999", {"display_name": "nope"})
    check("V-C-4  404 persona_not_found",
          code == 404 and body.get("detail") == "persona_not_found",
          f"code={code}")

    # ══════════════════════════════════════════════════════════════════════
    # V-C-5: POST /personas/{id}/select → set active
    # ══════════════════════════════════════════════════════════════════════
    section("V-C-5  POST /personas/{id}/select")
    code, body = http_post(f"/api/v1/personas/{p1_id}/select", {})
    check("V-C-5  select → 200 + selected_persona_id",
          code == 200 and body.get("selected_persona_id") == p1_id,
          f"code={code}")

    code, settings = http_get("/api/v1/settings")
    check("V-C-5b  GET /settings → selected_persona_id matches",
          settings.get("selected_persona_id") == p1_id,
          f"got={settings.get('selected_persona_id')}")

    # ══════════════════════════════════════════════════════════════════════
    # V-C-5c: Persona list includes is_active
    # ══════════════════════════════════════════════════════════════════════
    section("V-C-5c  Persona list is_active field")

    code, plist = http_get("/api/v1/personas")
    check("V-C-5c1  list → 200", code == 200 and isinstance(plist, list))

    # Created persona should have is_active field
    p1_in_list = next((p for p in (plist or []) if p.get("id") == p1_id), None)
    check("V-C-5c2  persona has is_active field",
          p1_in_list is not None and "is_active" in p1_in_list,
          f"keys={list(p1_in_list.keys()) if p1_in_list else None}")

    # After selecting p1, p1 should be active
    check("V-C-5c3  selected persona is_active=true",
          p1_in_list is not None and p1_in_list.get("is_active") is True,
          f"is_active={p1_in_list.get('is_active') if p1_in_list else None}")

    # Create a second persona - should be is_active=false
    code, p_temp = http_post("/api/v1/personas", {
        "display_name": "Temp Persona",
        "description": "Temporary for is_active test.",
    })
    assert code == 201
    p_temp_id = p_temp["id"]

    code, plist2 = http_get("/api/v1/personas")
    p_temp_in_list = next((p for p in (plist2 or []) if p.get("id") == p_temp_id), None)
    check("V-C-5c4  non-selected persona is_active=false",
          p_temp_in_list is not None and p_temp_in_list.get("is_active") is False,
          f"is_active={p_temp_in_list.get('is_active') if p_temp_in_list else None}")

    # Select p_temp, check p1 becomes inactive
    http_post(f"/api/v1/personas/{p_temp_id}/select", {})
    code, plist3 = http_get("/api/v1/personas")
    p1_now = next((p for p in (plist3 or []) if p.get("id") == p1_id), None)
    p_temp_now = next((p for p in (plist3 or []) if p.get("id") == p_temp_id), None)
    check("V-C-5c5  after switch: old persona is_active=false",
          p1_now is not None and p1_now.get("is_active") is False)
    check("V-C-5c6  after switch: new persona is_active=true",
          p_temp_now is not None and p_temp_now.get("is_active") is True)

    # Delete active persona - no persona should be active
    http_delete(f"/api/v1/personas/{p_temp_id}")
    code, plist4 = http_get("/api/v1/personas")
    all_inactive = all(p.get("is_active") is False for p in (plist4 or []))
    check("V-C-5c7  after deleting active: all personas is_active=false",
          all_inactive,
          f"active_list={[p.get('is_active') for p in (plist4 or [])]}")

    # Re-select p1 for subsequent tests
    http_post(f"/api/v1/personas/{p1_id}/select", {})

    # ══════════════════════════════════════════════════════════════════════
    # V-C-5c8-9: PATCH response includes correct is_active
    # ══════════════════════════════════════════════════════════════════════
    section("V-C-5c8  PATCH active persona → is_active=true")

    # p1 is selected - patching it should return is_active=true
    code, patched_active = http_patch(f"/api/v1/personas/{p1_id}", {
        "description": "Patched while active.",
    })
    check("V-C-5c8  PATCH active persona → is_active=true",
          code == 200
          and patched_active is not None
          and patched_active.get("is_active") is True,
          f"code={code} is_active={patched_active.get('is_active') if patched_active else None}")

    # Create another persona (inactive) and PATCH it
    code, p_inactive = http_post("/api/v1/personas", {
        "display_name": "Inactive For Patch",
        "description": "Should be inactive.",
    })
    assert code == 201
    p_inactive_id = p_inactive["id"]

    code, patched_inactive = http_patch(f"/api/v1/personas/{p_inactive_id}", {
        "description": "Patched while inactive.",
    })
    check("V-C-5c9  PATCH inactive persona → is_active=false",
          code == 200
          and patched_inactive is not None
          and patched_inactive.get("is_active") is False,
          f"code={code} is_active={patched_inactive.get('is_active') if patched_inactive else None}")

    # Verify GET /personas still shows exactly one active
    code, plist_after_patch = http_get("/api/v1/personas")
    active_count = sum(1 for p in (plist_after_patch or []) if p.get("is_active") is True)
    check("V-C-5c10  GET /personas still has exactly one active",
          active_count == 1,
          f"active_count={active_count}")

    # Clean up the temp inactive persona
    http_delete(f"/api/v1/personas/{p_inactive_id}")

    # Restore p1's original description for downstream injection tests
    http_patch(f"/api/v1/personas/{p1_id}", {
        "description": "I always respond sarcastically.",
    })

    # ══════════════════════════════════════════════════════════════════════
    # V-C-6: Persona injection in completion payload
    # ══════════════════════════════════════════════════════════════════════
    section("V-C-6  Persona injection in payload")

    # Create char + chat
    code, char_data = http_post_raw("/api/v1/characters/import",
                                    json.dumps({"name": "PartC Char",
                                                "system_prompt": "You are helpful.",
                                                "first_mes": "Hi!"}))
    assert code == 201
    test_char_id = char_data["id"]
    code, chat_data = http_post("/api/v1/chats", {"character_id": test_char_id})
    assert code == 201
    test_chat_id = chat_data["id"]

    _last_completion_body = None
    code, body = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "test message",
        "model_id": "openai/gpt-4",
    })
    check("V-C-6  completion succeeds with selected persona",
          code == 200, f"code={code}")

    if _last_completion_body:
        msgs = _last_completion_body.get("messages", [])
        system_msgs = [m for m in msgs if m.get("role") == "system"]
        persona_found = any("sarcastically" in m.get("content", "")
                           for m in system_msgs)
        check("V-C-6b  persona description injected as system message",
              persona_found,
              f"system_msgs={len(system_msgs)}")
    else:
        check("V-C-6b  persona description injected as system message",
              False, "no payload")

    # ══════════════════════════════════════════════════════════════════════
    # V-C-7: persona_id override in request body
    # ══════════════════════════════════════════════════════════════════════
    section("V-C-7  persona_id override in request")

    code, p2 = http_post("/api/v1/personas", {
        "display_name": "Beta Persona",
        "description": "I respond formally.",
    })
    assert code == 201
    p2_id = p2["id"]

    _last_completion_body = None
    code, body = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "test override",
        "model_id": "openai/gpt-4",
        "persona_id": p2_id,
    })
    check("V-C-7  completion with persona_id override succeeds",
          code == 200, f"code={code}")

    if _last_completion_body:
        msgs = _last_completion_body.get("messages", [])
        system_msgs = [m for m in msgs if m.get("role") == "system"]
        beta_found = any("formally" in m.get("content", "")
                        for m in system_msgs)
        alpha_found = any("sarcastically" in m.get("content", "")
                         for m in system_msgs)
        check("V-C-7b  override persona injected (Beta)",
              beta_found, f"found={beta_found}")
        check("V-C-7c  selected persona NOT injected (Alpha)",
              not alpha_found)
    else:
        check("V-C-7b  override persona injected (Beta)", False, "no payload")
        check("V-C-7c  selected persona NOT injected (Alpha)", False, "no payload")

    # ══════════════════════════════════════════════════════════════════════
    # V-C-8: persona_id for nonexistent persona → 404
    # ══════════════════════════════════════════════════════════════════════
    section("V-C-8  persona_id=999999 → 404")
    code, body = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "test",
        "model_id": "openai/gpt-4",
        "persona_id": 999999,
    })
    check("V-C-8  nonexistent persona_id → 404",
          code == 404 and body.get("detail") == "persona_not_found",
          f"code={code}")

    # ══════════════════════════════════════════════════════════════════════
    # V-C-9: DELETE /personas/{id} → clears selection
    # ══════════════════════════════════════════════════════════════════════
    section("V-C-9  DELETE persona clears selection")

    # First select p1 again
    http_post(f"/api/v1/personas/{p1_id}/select", {})
    code, body = http_delete(f"/api/v1/personas/{p1_id}")
    check("V-C-9  delete selected persona → 200",
          code == 200, f"code={code}")

    code, settings = http_get("/api/v1/settings")
    check("V-C-9b  selected_persona_id cleared after delete",
          settings.get("selected_persona_id") is None,
          f"got={settings.get('selected_persona_id')}")

    # ══════════════════════════════════════════════════════════════════════
    # V-C-10: No persona → no persona system message
    # ══════════════════════════════════════════════════════════════════════
    section("V-C-10  No persona → no persona injection")

    _last_completion_body = None
    code, body = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "no persona test",
        "model_id": "openai/gpt-4",
    })
    check("V-C-10  completion without persona succeeds",
          code == 200, f"code={code}")

    if _last_completion_body:
        msgs = _last_completion_body.get("messages", [])
        system_msgs = [m for m in msgs if m.get("role") == "system"]
        no_persona_content = not any("sarcastically" in m.get("content", "")
                                    or "formally" in m.get("content", "")
                                    for m in system_msgs)
        check("V-C-10b  no persona description in system messages",
              no_persona_content)
    else:
        check("V-C-10b  no persona description in system messages",
              False, "no payload")

    # ══════════════════════════════════════════════════════════════════════
    # V-C-11: Empty display_name → 422
    # ══════════════════════════════════════════════════════════════════════
    section("V-C-11  Empty display_name → 422")
    code, body = http_post("/api/v1/personas", {"display_name": "", "description": "x"})
    check("V-C-11  empty name → 422",
          code == 422, f"code={code}")

    code, body = http_post("/api/v1/personas", {"display_name": "   "})
    check("V-C-11b  whitespace name → 422",
          code == 422, f"code={code}")

    # ══════════════════════════════════════════════════════════════════════
    # V-C-12: Persona description never in completion response body
    # ══════════════════════════════════════════════════════════════════════
    section("V-C-12  Privacy: persona description not in response")

    # Select Beta persona for a completion
    http_post(f"/api/v1/personas/{p2_id}/select", {})
    code, resp = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "privacy test",
        "model_id": "openai/gpt-4",
    })
    resp_str = json.dumps(resp) if resp else ""
    check("V-C-12  'formally' not in completion response",
          "formally" not in resp_str)

    # Cleanup p2
    http_delete(f"/api/v1/personas/{p2_id}")

finally:
    section("Cleanup")

    if server_proc and server_proc.poll() is None:
        server_proc.terminate()
        server_proc.wait(timeout=5)
    if fake_server:
        fake_server.shutdown()

    try:
        if _saved_api_key:
            keyring.set_password(_KR_SERVICE, _KR_API_KEY, _saved_api_key)
        else:
            try:
                keyring.delete_password(_KR_SERVICE, _KR_API_KEY)
            except keyring.errors.PasswordDeleteError:
                pass
    except Exception:
        pass

    if _db_existed:
        try:
            with sqlite3.connect(_db_path) as _con:
                for mid in ({r[0] for r in _con.execute("SELECT id FROM messages").fetchall()} - _existing_msg_ids):
                    _con.execute("DELETE FROM messages WHERE id = ?", (mid,))
                for cid in ({r[0] for r in _con.execute("SELECT id FROM chats").fetchall()} - _existing_chat_ids):
                    _con.execute("DELETE FROM chats WHERE id = ?", (cid,))
                for chid in ({r[0] for r in _con.execute("SELECT id FROM characters").fetchall()} - _existing_char_ids):
                    _con.execute("DELETE FROM characters WHERE id = ?", (chid,))
                try:
                    for pid in ({r[0] for r in _con.execute("SELECT id FROM personas").fetchall()} - _existing_persona_ids):
                        _con.execute("DELETE FROM personas WHERE id = ?", (pid,))
                except Exception:
                    pass
                _con.execute("DELETE FROM settings WHERE key = 'selected_persona_id'")
                _con.commit()
        except Exception:
            pass
    print("  [info] Cleanup done.")

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
