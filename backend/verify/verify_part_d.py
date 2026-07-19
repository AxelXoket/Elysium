"""
Part D verification - Character PATCH + DELETE.
Run from backend/:
    .venv/Scripts/python verify_part_d.py
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


def http_get(path, timeout=10):
    url = f"{BASE}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


def http_post(path, data, timeout=10):
    url = f"{BASE}{path}"
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


def http_post_raw(path, raw_body, timeout=10):
    url = f"{BASE}{path}"
    body = raw_body.encode() if isinstance(raw_body, str) else raw_body
    req = urllib.request.Request(url, data=body, method="POST",
                                headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


def http_patch(path, data, timeout=10):
    url = f"{BASE}{path}"
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method="PATCH",
                                headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


def http_delete(path, timeout=10):
    url = f"{BASE}{path}"
    req = urllib.request.Request(url, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


# Fake server (minimal)
FAKE_PORT = 19881

FAKE_MODEL = {
    "id": "openai/gpt-4", "name": "GPT-4", "description": "Test",
    "context_length": 128000,
    "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
    "pricing": {"prompt": "0.00003", "completion": "0.00006"},
    "top_provider": {"max_completion_tokens": 4096, "context_length": 128000},
    "supported_parameters": ["temperature", "top_p", "max_tokens"],
    "created": 1700000000, "canonical_slug": "openai/gpt-4",
}


class _FakeHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/api/v1/models/user", "/api/v1/models"):
            self._json(200, {"data": [FAKE_MODEL]})
        elif self.path == "/api/v1/key":
            self._json(200, {"data": {"label": "k"}})
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        if self.path == "/api/v1/chat/completions":
            l = int(self.headers.get("Content-Length", 0)); self.rfile.read(l)
            self._json(200, {"choices": [{"message": {"role": "assistant", "content": "ok"}}]})
        else:
            self.send_response(404); self.end_headers()

    def _json(self, code, data):
        b = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)

    def log_message(self, *a): pass


_db_path = os.path.join(BACKEND_DIR, "app.db")
_db_existed = os.path.exists(_db_path)
_existing_char_ids = set()
_existing_chat_ids = set()
_existing_msg_ids = set()
if _db_existed:
    try:
        with sqlite3.connect(_db_path) as c:
            _existing_char_ids = {r[0] for r in c.execute("SELECT id FROM characters").fetchall()}
            _existing_chat_ids = {r[0] for r in c.execute("SELECT id FROM chats").fetchall()}
            _existing_msg_ids = {r[0] for r in c.execute("SELECT id FROM messages").fetchall()}
    except Exception:
        pass

import keyring, keyring.errors
_KR_S = "chatbot_interface"; _KR_K = "openrouter_api_key"
_saved_key = keyring.get_password(_KR_S, _KR_K)

_uv = "uvicorn.exe" if sys.platform == "win32" else "uvicorn"
UVICORN = os.path.join(os.path.dirname(sys.executable), _uv)
server_proc = None
fake_server = None

try:
    urllib.request.urlopen(f"{BASE}/healthz", timeout=1)
    print("[FATAL] port in use"); sys.exit(1)
except Exception:
    pass

try:
    fake_server = http.server.HTTPServer(("127.0.0.1", FAKE_PORT), _FakeHandler)
    threading.Thread(target=fake_server.serve_forever, daemon=True).start()

    env = os.environ.copy()
    env["OPENROUTER_BASE_URL"] = f"http://127.0.0.1:{FAKE_PORT}/api/v1"
    server_proc = subprocess.Popen(
        [UVICORN, "main:app", "--host", "127.0.0.1", "--port", "8787", "--log-level", "warning"],
        cwd=BACKEND_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    for _ in range(30):
        time.sleep(0.2)
        try:
            urllib.request.urlopen(f"{BASE}/healthz", timeout=1); break
        except Exception:
            pass
    else:
        print("[FATAL] start failed"); sys.exit(1)

    http_post("/api/v1/settings/api-key", {"api_key": "sk-test-d"})
    http_get("/api/v1/models/openrouter")

    # ══════════════════════════════════════════════════════════════════════
    # V-D-1: PATCH /characters/{id}
    # ══════════════════════════════════════════════════════════════════════
    section("V-D-1  PATCH /characters/{id}")

    code, ch = http_post_raw("/api/v1/characters/import",
                             json.dumps({"name": "D-Test", "system_prompt": "sys",
                                         "first_mes": "hello", "description": "old desc"}))
    assert code == 201
    ch_id = ch["id"]

    code, patched = http_patch(f"/api/v1/characters/{ch_id}", {"description": "new desc"})
    check("V-D-1  patch → 200 + updated field",
          code == 200 and patched.get("description") == "new desc",
          f"code={code}")
    check("V-D-1b  unchanged field preserved",
          patched.get("name") == "D-Test")
    check("V-D-1c  system_prompt unchanged",
          patched.get("system_prompt") == "sys")

    # ══════════════════════════════════════════════════════════════════════
    # V-D-2: PATCH empty name → 422
    # ══════════════════════════════════════════════════════════════════════
    section("V-D-2  PATCH empty name")
    code, _ = http_patch(f"/api/v1/characters/{ch_id}", {"name": ""})
    check("V-D-2  empty name → 422", code == 422, f"code={code}")

    code, _ = http_patch(f"/api/v1/characters/{ch_id}", {"name": "   "})
    check("V-D-2b  whitespace name → 422", code == 422, f"code={code}")

    # ══════════════════════════════════════════════════════════════════════
    # V-D-3: PATCH nonexistent → 404
    # ══════════════════════════════════════════════════════════════════════
    section("V-D-3  PATCH nonexistent")
    code, body = http_patch("/api/v1/characters/999999", {"name": "x"})
    check("V-D-3  404 character_not_found",
          code == 404 and body.get("detail") == "character_not_found",
          f"code={code}")

    # ══════════════════════════════════════════════════════════════════════
    # V-D-4: PATCH tags
    # ══════════════════════════════════════════════════════════════════════
    section("V-D-4  PATCH tags")
    code, patched = http_patch(f"/api/v1/characters/{ch_id}", {"tags": ["a", "b"]})
    check("V-D-4  tags updated",
          code == 200 and patched.get("tags") == ["a", "b"],
          f"tags={patched.get('tags') if patched else None}")

    # ══════════════════════════════════════════════════════════════════════
    # V-D-5: DELETE /characters/{id} cascade
    # ══════════════════════════════════════════════════════════════════════
    section("V-D-5  DELETE /characters/{id} cascade")

    # Create chat + messages
    code, chat = http_post("/api/v1/chats", {"character_id": ch_id})
    assert code == 201
    chat_id = chat["id"]

    code, _ = http_post(f"/api/v1/chats/{chat_id}/complete", {
        "message": "test msg", "model_id": "openai/gpt-4"})
    assert code == 200

    # Verify data exists
    with sqlite3.connect(_db_path) as con:
        msg_before = con.execute(
            "SELECT count(*) FROM messages WHERE chat_id = ?", (chat_id,)
        ).fetchone()[0]
    check("V-D-5a  messages exist before delete",
          msg_before > 0, f"count={msg_before}")

    code, body = http_delete(f"/api/v1/characters/{ch_id}")
    check("V-D-5b  delete → 200",
          code == 200 and body.get("ok") is True, f"code={code}")

    # Verify cascade
    code, body = http_get(f"/api/v1/characters/{ch_id}")
    check("V-D-5c  character gone → 404",
          code == 404, f"code={code}")

    code, body = http_get(f"/api/v1/chats/{chat_id}")
    check("V-D-5d  chat gone → 404",
          code == 404, f"code={code}")

    with sqlite3.connect(_db_path) as con:
        msg_after = con.execute(
            "SELECT count(*) FROM messages WHERE chat_id = ?", (chat_id,)
        ).fetchone()[0]
    check("V-D-5e  messages deleted",
          msg_after == 0, f"count={msg_after}")

    # ══════════════════════════════════════════════════════════════════════
    # V-D-6: DELETE nonexistent → 404
    # ══════════════════════════════════════════════════════════════════════
    section("V-D-6  DELETE nonexistent")
    code, body = http_delete("/api/v1/characters/999999")
    check("V-D-6  404 character_not_found",
          code == 404 and body.get("detail") == "character_not_found",
          f"code={code}")

    # ══════════════════════════════════════════════════════════════════════
    # V-D-7: raw_json never in PATCH response
    # ══════════════════════════════════════════════════════════════════════
    section("V-D-7  raw_json privacy")
    code, ch2 = http_post_raw("/api/v1/characters/import",
                              json.dumps({"name": "D-Priv", "system_prompt": "x",
                                          "first_mes": "hi", "secret_field": "leaked"}))
    assert code == 201
    ch2_id = ch2["id"]
    code, patched = http_patch(f"/api/v1/characters/{ch2_id}", {"description": "updated"})
    resp_str = json.dumps(patched)
    check("V-D-7  raw_json not in PATCH response",
          "raw_json" not in resp_str and "secret_field" not in resp_str)
    # cleanup
    http_delete(f"/api/v1/characters/{ch2_id}")

finally:
    section("Cleanup")
    if server_proc and server_proc.poll() is None:
        server_proc.terminate(); server_proc.wait(timeout=5)
    if fake_server:
        fake_server.shutdown()
    try:
        if _saved_key:
            keyring.set_password(_KR_S, _KR_K, _saved_key)
        else:
            try: keyring.delete_password(_KR_S, _KR_K)
            except keyring.errors.PasswordDeleteError: pass
    except Exception:
        pass
    if _db_existed:
        try:
            with sqlite3.connect(_db_path) as c:
                for mid in ({r[0] for r in c.execute("SELECT id FROM messages").fetchall()} - _existing_msg_ids):
                    c.execute("DELETE FROM messages WHERE id = ?", (mid,))
                for cid in ({r[0] for r in c.execute("SELECT id FROM chats").fetchall()} - _existing_chat_ids):
                    c.execute("DELETE FROM chats WHERE id = ?", (cid,))
                for chid in ({r[0] for r in c.execute("SELECT id FROM characters").fetchall()} - _existing_char_ids):
                    c.execute("DELETE FROM characters WHERE id = ?", (chid,))
                c.commit()
        except Exception:
            pass
    print("  [info] Cleanup done.")

section("Summary")
p = sum(1 for _, ok in results if ok)
t = len(results)
print(f"  {p}/{t} checks passed\n")
if p < t:
    print("  FAILED:")
    for l, ok in results:
        if not ok: print(f"    x {l}")
    print()
sys.exit(0 if p == t else 1)
