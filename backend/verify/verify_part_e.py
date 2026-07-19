"""
Part E verification - Chat/Message Lifecycle (Hotfix).
Run from backend/:
    .venv/Scripts/python verify_part_e.py
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


def check(label, ok, detail=""):
    tag = PASS if ok else FAIL
    msg = f"  [{tag}] {label}"
    if detail:
        msg += f"  ->  {detail}"
    print(msg)
    results.append((label, ok))


def section(title):
    print(f"\n{'-'*62}\n  {title}\n{'-'*62}")


BASE = "http://127.0.0.1:8787"


def http_get(path, timeout=10):
    url = f"{BASE}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode())
        except: return e.code, {}


def http_post(path, data, timeout=10):
    url = f"{BASE}{path}"
    b = json.dumps(data).encode()
    req = urllib.request.Request(url, data=b, method="POST",
                                headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode())
        except: return e.code, {}


def http_post_raw(path, raw_body, timeout=10):
    url = f"{BASE}{path}"
    b = raw_body.encode() if isinstance(raw_body, str) else raw_body
    req = urllib.request.Request(url, data=b, method="POST",
                                headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode())
        except: return e.code, {}


def http_delete(path, timeout=10):
    url = f"{BASE}{path}"
    req = urllib.request.Request(url, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode())
        except: return e.code, {}


# Fake server
FAKE_PORT = 19882
_completion_counter = 0
_last_completion_body = None

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
        global _completion_counter, _last_completion_body
        if self.path == "/api/v1/chat/completions":
            l = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(l)
            try:
                _last_completion_body = json.loads(raw)
            except Exception:
                _last_completion_body = {}
            _completion_counter += 1
            self._json(200, {"choices": [{"message": {"role": "assistant",
                                                       "content": f"Reply #{_completion_counter}"}}]})
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
_existing_persona_ids = set()
if _db_existed:
    try:
        with sqlite3.connect(_db_path) as c:
            _existing_char_ids = {r[0] for r in c.execute("SELECT id FROM characters").fetchall()}
            _existing_chat_ids = {r[0] for r in c.execute("SELECT id FROM chats").fetchall()}
            _existing_msg_ids = {r[0] for r in c.execute("SELECT id FROM messages").fetchall()}
            try:
                _existing_persona_ids = {r[0] for r in c.execute("SELECT id FROM personas").fetchall()}
            except Exception:
                pass
    except Exception: pass

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
except Exception: pass

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
        except Exception: pass
    else:
        print("[FATAL] start failed"); sys.exit(1)

    http_post("/api/v1/settings/api-key", {"api_key": "sk-test-e"})
    http_get("/api/v1/models/openrouter")

    # Setup: char + chat + some messages
    code, ch = http_post_raw("/api/v1/characters/import",
                             json.dumps({"name": "E-Test", "system_prompt": "sys",
                                         "first_mes": "hi"}))
    assert code == 201
    ch_id = ch["id"]

    code, chat = http_post("/api/v1/chats", {"character_id": ch_id})
    assert code == 201
    chat_id = chat["id"]

    # Send a few messages: first_mes(asst) + user1+asst1 + user2+asst2
    code, r1 = http_post(f"/api/v1/chats/{chat_id}/complete",
                         {"message": "msg1", "model_id": "openai/gpt-4"})
    assert code == 200
    user1_id = r1["user_message"]["id"]
    asst1_id = r1["assistant_message"]["id"]

    code, r2 = http_post(f"/api/v1/chats/{chat_id}/complete",
                         {"message": "msg2", "model_id": "openai/gpt-4"})
    assert code == 200
    user2_id = r2["user_message"]["id"]
    asst2_id = r2["assistant_message"]["id"]

    # ══════════════════════════════════════════════════════════════════════
    # V-E-1: DELETE /chats/{id}/messages/{msg_id} - delete target + following
    # ══════════════════════════════════════════════════════════════════════
    section("V-E-1  DELETE message → target + following")

    # Delete asst1 - should delete asst1, user2, asst2 (3 messages)
    code, body = http_delete(f"/api/v1/chats/{chat_id}/messages/{asst1_id}")
    check("V-E-1  delete → 200 + ok",
          code == 200 and body.get("ok") is True, f"code={code}")
    check("V-E-1b  deleted_count >= 1",
          body.get("deleted_count", 0) >= 1,
          f"deleted_count={body.get('deleted_count')}")
    check("V-E-1c  deleted_count == 3 (asst1, user2, asst2)",
          body.get("deleted_count") == 3,
          f"deleted_count={body.get('deleted_count')}")

    code, msgs = http_get(f"/api/v1/chats/{chat_id}/messages")
    msg_ids = [m["id"] for m in msgs]
    check("V-E-1d  asst1 gone", asst1_id not in msg_ids, f"ids={msg_ids}")
    check("V-E-1e  user2 gone", user2_id not in msg_ids, f"ids={msg_ids}")
    check("V-E-1f  asst2 gone", asst2_id not in msg_ids, f"ids={msg_ids}")
    # user1 and first_mes should remain
    check("V-E-1g  user1 preserved", user1_id in msg_ids, f"ids={msg_ids}")

    # ══════════════════════════════════════════════════════════════════════
    # V-E-2: DELETE last message only
    # ══════════════════════════════════════════════════════════════════════
    section("V-E-2  DELETE last message only")

    # Re-send messages for further tests
    code, r3 = http_post(f"/api/v1/chats/{chat_id}/complete",
                         {"message": "msg3", "model_id": "openai/gpt-4"})
    assert code == 200
    user3_id = r3["user_message"]["id"]
    asst3_id = r3["assistant_message"]["id"]

    code, body = http_delete(f"/api/v1/chats/{chat_id}/messages/{asst3_id}")
    check("V-E-2  delete last → deleted_count == 1",
          code == 200 and body.get("deleted_count") == 1,
          f"deleted_count={body.get('deleted_count')}")

    code, msgs = http_get(f"/api/v1/chats/{chat_id}/messages")
    msg_ids = [m["id"] for m in msgs]
    check("V-E-2b  last msg gone, user3 preserved",
          asst3_id not in msg_ids and user3_id in msg_ids)

    # ══════════════════════════════════════════════════════════════════════
    # V-E-3: DELETE does not affect other chats
    # ══════════════════════════════════════════════════════════════════════
    section("V-E-3  DELETE isolation between chats")

    code, chat_b = http_post("/api/v1/chats", {"character_id": ch_id})
    assert code == 201
    chat_b_id = chat_b["id"]
    code, rb = http_post(f"/api/v1/chats/{chat_b_id}/complete",
                         {"message": "other", "model_id": "openai/gpt-4"})
    assert code == 200
    other_asst_id = rb["assistant_message"]["id"]

    # Delete user3 from chat_id - should not touch chat_b
    code, body = http_delete(f"/api/v1/chats/{chat_id}/messages/{user3_id}")
    check("V-E-3  delete from chat A", code == 200)

    code, msgs_b = http_get(f"/api/v1/chats/{chat_b_id}/messages")
    other_ids = [m["id"] for m in msgs_b]
    check("V-E-3b  chat B messages untouched",
          other_asst_id in other_ids)

    # ══════════════════════════════════════════════════════════════════════
    # V-E-4: DELETE message wrong chat → 404
    # ══════════════════════════════════════════════════════════════════════
    section("V-E-4  DELETE wrong chat → 404")
    code, body = http_delete(f"/api/v1/chats/999999/messages/{other_asst_id}")
    check("V-E-4  wrong chat → 404", code == 404, f"code={code}")

    # ══════════════════════════════════════════════════════════════════════
    # V-E-5: DELETE nonexistent message → 404
    # ══════════════════════════════════════════════════════════════════════
    section("V-E-5  DELETE nonexistent message → 404")
    code, body = http_delete(f"/api/v1/chats/{chat_id}/messages/999999")
    check("V-E-5  → 404 message_not_found",
          code == 404 and body.get("detail") == "message_not_found",
          f"code={code} detail={body.get('detail')}")

    # ══════════════════════════════════════════════════════════════════════
    # V-E-6: POST /chats/{id}/clear → deleted_count
    # ══════════════════════════════════════════════════════════════════════
    section("V-E-6  POST /chats/{id}/clear")

    # Create a fresh chat for clear test
    code, chat2 = http_post("/api/v1/chats", {"character_id": ch_id})
    assert code == 201
    chat2_id = chat2["id"]

    http_post(f"/api/v1/chats/{chat2_id}/complete",
              {"message": "clear1", "model_id": "openai/gpt-4"})
    http_post(f"/api/v1/chats/{chat2_id}/complete",
              {"message": "clear2", "model_id": "openai/gpt-4"})

    code, msgs_before = http_get(f"/api/v1/chats/{chat2_id}/messages")
    before_count = len(msgs_before)

    code, body = http_post(f"/api/v1/chats/{chat2_id}/clear", {})
    check("V-E-6  clear → 200 + ok",
          code == 200 and body.get("ok") is True, f"code={code}")
    check("V-E-6b  deleted_count > 0",
          body.get("deleted_count", 0) > 0,
          f"deleted_count={body.get('deleted_count')}")
    check("V-E-6c  deleted_count == before_count",
          body.get("deleted_count") == before_count,
          f"deleted_count={body.get('deleted_count')} expected={before_count}")

    code, msgs_after = http_get(f"/api/v1/chats/{chat2_id}/messages")
    check("V-E-6d  no messages after clear",
          len(msgs_after) == 0, f"count={len(msgs_after)}")

    # Chat still exists
    code, _ = http_get(f"/api/v1/chats/{chat2_id}")
    check("V-E-6e  chat still exists after clear",
          code == 200, f"code={code}")

    # ══════════════════════════════════════════════════════════════════════
    # V-E-7: DELETE /chats/{id}
    # ══════════════════════════════════════════════════════════════════════
    section("V-E-7  DELETE /chats/{id}")

    code, body = http_delete(f"/api/v1/chats/{chat2_id}")
    check("V-E-7  delete chat → 200 + ok",
          code == 200 and body.get("ok") is True, f"code={code}")

    code, _ = http_get(f"/api/v1/chats/{chat2_id}")
    check("V-E-7b  chat gone → 404", code == 404, f"code={code}")

    code, _ = http_get(f"/api/v1/chats/{chat2_id}/messages")
    check("V-E-7c  messages gone → 404", code == 404, f"code={code}")

    # ══════════════════════════════════════════════════════════════════════
    # V-E-8: DELETE nonexistent chat → 404
    # ══════════════════════════════════════════════════════════════════════
    section("V-E-8  DELETE nonexistent chat")
    code, body = http_delete("/api/v1/chats/999999")
    check("V-E-8  → 404 chat_not_found",
          code == 404 and body.get("detail") == "chat_not_found",
          f"code={code}")

    # ══════════════════════════════════════════════════════════════════════
    # V-E-9: Regenerate - core lifecycle
    # ══════════════════════════════════════════════════════════════════════
    section("V-E-9  Regenerate lifecycle")

    # Use chat_b which has first_mes + user+asst
    code, msgs = http_get(f"/api/v1/chats/{chat_b_id}/messages")
    msg_count_before = len(msgs)
    asst_msgs = [m for m in msgs if m["role"] == "assistant"]
    check("V-E-9a  has assistant messages",
          len(asst_msgs) >= 1, f"count={len(asst_msgs)}")

    if asst_msgs:
        target_asst = asst_msgs[-1]
        target_id = target_asst["id"]
        old_content = target_asst["content"]

        # Find the user message before it
        user_msgs_before = [m for m in msgs if m["role"] == "user" and m["id"] < target_id]
        assert len(user_msgs_before) >= 1
        existing_user_id = user_msgs_before[-1]["id"]

        code, regen = http_post(
            f"/api/v1/chats/{chat_b_id}/messages/{target_id}/regenerate",
            {"model_id": "openai/gpt-4"})
        check("V-E-9b  regenerate → 200",
              code == 200, f"code={code}")

        if code == 200:
            new_asst = regen.get("assistant_message", {})
            new_user = regen.get("user_message", {})

            check("V-E-9c  new assistant message returned",
                  new_asst.get("content") is not None)

            # V-E-17: user_message.id is the EXISTING id
            check("V-E-17  user_message.id == existing user id",
                  new_user.get("id") == existing_user_id,
                  f"got={new_user.get('id')} expected={existing_user_id}")

            # Assistant id should be new (different from old)
            check("V-E-9d  assistant_message.id is NEW",
                  new_asst.get("id") != target_id,
                  f"new={new_asst.get('id')} old={target_id}")

            # V-E-16b: message count stays the same
            code, msgs_after = http_get(f"/api/v1/chats/{chat_b_id}/messages")
            msg_count_after = len(msgs_after)
            check("V-E-16b  message count unchanged after regenerate",
                  msg_count_after == msg_count_before,
                  f"before={msg_count_before} after={msg_count_after}")

            # No duplicate user message
            user_ids_after = [m["id"] for m in msgs_after if m["role"] == "user"]
            check("V-E-16b2  no duplicate user messages",
                  len(user_ids_after) == len(set(user_ids_after)),
                  f"user_ids={user_ids_after}")
            check("V-E-16b3  existing user still present",
                  existing_user_id in user_ids_after)

    # ══════════════════════════════════════════════════════════════════════
    # V-E-10: Regenerate non-assistant → 422
    # ══════════════════════════════════════════════════════════════════════
    section("V-E-10  Regenerate non-assistant message")

    code, msgs = http_get(f"/api/v1/chats/{chat_b_id}/messages")
    user_msgs = [m for m in msgs if m["role"] == "user"]
    if user_msgs:
        code, body = http_post(
            f"/api/v1/chats/{chat_b_id}/messages/{user_msgs[0]['id']}/regenerate",
            {"model_id": "openai/gpt-4"})
        check("V-E-10  user message → 422 not_last_assistant_message",
              code == 422 and body.get("detail") == "not_last_assistant_message",
              f"code={code} detail={body.get('detail')}")
    else:
        check("V-E-10  user message → 422", False, "no user messages")

    # ══════════════════════════════════════════════════════════════════════
    # V-E-11: Regenerate non-latest assistant → 422
    # ══════════════════════════════════════════════════════════════════════
    section("V-E-11  Regenerate non-latest assistant")

    # Send another message so there are 2 assistant messages
    code, r_extra = http_post(f"/api/v1/chats/{chat_b_id}/complete",
                              {"message": "extra", "model_id": "openai/gpt-4"})
    assert code == 200

    code, msgs = http_get(f"/api/v1/chats/{chat_b_id}/messages")
    asst_msgs = [m for m in msgs if m["role"] == "assistant"]
    if len(asst_msgs) >= 2:
        # Try to regenerate the first (non-latest) assistant
        first_asst_id = asst_msgs[0]["id"]
        code, body = http_post(
            f"/api/v1/chats/{chat_b_id}/messages/{first_asst_id}/regenerate",
            {"model_id": "openai/gpt-4"})
        check("V-E-11  non-latest assistant → 422 not_last_assistant_message",
              code == 422 and body.get("detail") == "not_last_assistant_message",
              f"code={code} detail={body.get('detail')}")
    else:
        check("V-E-11  non-latest assistant → 422", False, "need 2+ assistants")

    # ══════════════════════════════════════════════════════════════════════
    # V-E-12: Regenerate first_mes only (no preceding user) → 422
    # ══════════════════════════════════════════════════════════════════════
    section("V-E-12  Regenerate first_mes (no user) → 422")

    code, chat3 = http_post("/api/v1/chats", {"character_id": ch_id})
    assert code == 201
    chat3_id = chat3["id"]

    code, msgs3 = http_get(f"/api/v1/chats/{chat3_id}/messages")
    if len(msgs3) == 1 and msgs3[0]["role"] == "assistant":
        code, body = http_post(
            f"/api/v1/chats/{chat3_id}/messages/{msgs3[0]['id']}/regenerate",
            {"model_id": "openai/gpt-4"})
        check("V-E-12  first_mes regenerate → 422 no_preceding_user_message",
              code == 422 and body.get("detail") == "no_preceding_user_message",
              f"code={code} detail={body.get('detail')}")
    else:
        check("V-E-12  first_mes regenerate → 422", False,
              f"expected 1 asst, got {len(msgs3)}")

    # ══════════════════════════════════════════════════════════════════════
    # V-E-13: Regenerate nonexistent → 404
    # ══════════════════════════════════════════════════════════════════════
    section("V-E-13  Regenerate nonexistent message")
    code, body = http_post(
        f"/api/v1/chats/{chat_b_id}/messages/999999/regenerate",
        {"model_id": "openai/gpt-4"})
    check("V-E-13  → 404",
          code == 404 and body.get("detail") == "message_not_found",
          f"code={code}")

    # ══════════════════════════════════════════════════════════════════════
    # V-E-14: Regenerate payload - privacy checks
    # ══════════════════════════════════════════════════════════════════════
    section("V-E-14  Regenerate payload privacy")

    # Create persona, select it, then regenerate
    code, persona = http_post("/api/v1/personas",
                              {"display_name": "E-Persona", "description": "regen-persona-text"})
    assert code == 201
    persona_id = persona["id"]
    http_post(f"/api/v1/personas/{persona_id}/select", {})

    # Get latest assistant from chat_b
    code, msgs = http_get(f"/api/v1/chats/{chat_b_id}/messages")
    asst_msgs = [m for m in msgs if m["role"] == "assistant"]
    if asst_msgs:
        last_asst = asst_msgs[-1]
        _last_completion_body = None
        code, regen = http_post(
            f"/api/v1/chats/{chat_b_id}/messages/{last_asst['id']}/regenerate",
            {"model_id": "openai/gpt-4"})
        check("V-E-14a  regenerate with persona → 200",
              code == 200, f"code={code}")

        if _last_completion_body:
            payload_msgs = _last_completion_body.get("messages", [])
            system_msgs = [m for m in payload_msgs if m.get("role") == "system"]
            persona_found = any("regen-persona-text" in m.get("content", "")
                               for m in system_msgs)
            check("V-E-14b  persona injected in regenerate payload",
                  persona_found)

            # context_budget_tokens not in payload
            check("V-E-14c  no context_budget_tokens in payload",
                  "context_budget_tokens" not in json.dumps(_last_completion_body))

            # provider policy
            provider = _last_completion_body.get("provider", {})
            check("V-E-14d  provider.zdr=true",
                  provider.get("zdr") is True)
            check("V-E-14e  provider.data_collection=deny",
                  provider.get("data_collection") == "deny")
            check("V-E-14f  provider.allow_fallbacks=false",
                  provider.get("allow_fallbacks") is False)
        else:
            for suffix in ("b", "c", "d", "e", "f"):
                check(f"V-E-14{suffix}  payload check", False, "no payload captured")

    # Cleanup persona
    http_delete(f"/api/v1/personas/{persona_id}")

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
    except Exception: pass
    if _db_existed:
        try:
            with sqlite3.connect(_db_path) as c:
                for mid in ({r[0] for r in c.execute("SELECT id FROM messages").fetchall()} - _existing_msg_ids):
                    c.execute("DELETE FROM messages WHERE id = ?", (mid,))
                for cid in ({r[0] for r in c.execute("SELECT id FROM chats").fetchall()} - _existing_chat_ids):
                    c.execute("DELETE FROM chats WHERE id = ?", (cid,))
                for chid in ({r[0] for r in c.execute("SELECT id FROM characters").fetchall()} - _existing_char_ids):
                    c.execute("DELETE FROM characters WHERE id = ?", (chid,))
                try:
                    for pid in ({r[0] for r in c.execute("SELECT id FROM personas").fetchall()} - _existing_persona_ids):
                        c.execute("DELETE FROM personas WHERE id = ?", (pid,))
                    c.execute("DELETE FROM settings WHERE key = 'selected_persona_id'")
                except Exception: pass
                c.commit()
        except Exception: pass
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
