"""
Part A verification script - Baseline + Error Contract + Frontend Contract Scaffold.
Run from backend/ with the virtual environment active:
    .venv/Scripts/python verify_part_a.py

Tests:
  V-A-1: verify_phase5b.py still passes (subprocess, exit code 0).
  V-A-2: POST /complete with fake 402 → detail="openrouter_insufficient_credits".
  V-A-3: POST /complete with fake 403 → detail is a short error code (not raw body).
  V-A-4: Error response never contains raw upstream body.
  V-A-5: docs/frontend_contract.md exists and contains required sections.
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

# The scripts moved from backend/ into backend/verify/ (commit d8da7db) and
# this line did not, so the whole grep suite walked its OWN directory: it
# scanned 12 files, every one of them in the VERIFY_FILES exclusion set, and
# reported PASS on privacy assertions that had examined no application code
# at all. Adding `allow_origins=["*"]` to main.py still printed PASS.
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

# Every script in this directory hardcoded `BACKEND_DIR/app.db` and ran
# DELETE statements against it. In a dev tree that path IS the developer's
# live vault, so the whole suite mutated real data on every run. _harness
# redirects the child process, and this file's own direct DB access, at a
# fresh temp directory. See verify/_harness.py.
import _harness


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


def http_delete(path: str) -> tuple[int, dict | None]:
    url = f"{BASE}{path}"
    req = urllib.request.Request(url, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


# ── Fake OpenRouter server ────────────────────────────────────────────────────
FAKE_PORT = 19878  # unique port for Part A

_fake_mode = "normal"

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
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        if self.path == "/api/v1/chat/completions":
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)  # consume body
            self._handle_completion()
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_completion(self) -> None:
        if _fake_mode == "normal":
            self._json(200, {
                "choices": [{"message": {"role": "assistant",
                                         "content": "Fake reply."}}]
            })
        elif _fake_mode == "credits_402":
            self._json(402, {"error": {"message": "insufficient credits",
                                        "code": 402}})
        elif _fake_mode == "auth_403":
            self._json(403, {"error": {"message": "forbidden by provider",
                                        "code": 403}})
        elif _fake_mode == "moderation_403":
            # OpenRouter's documented ModerationErrorMetadata. `flagged_input`
            # is the user's own prompt and must never come back out.
            self._json(403, {"error": {
                "message": "flagged by the provider's moderation",
                "code": 403,
                "metadata": {
                    "reasons": ["harassment"],
                    "flagged_input": "RAW_PROMPT_MUST_NOT_LEAK",
                    "provider_name": "SomeProvider",
                    "model_slug": "openai/gpt-4",
                },
            }})
        else:
            self._json(200, {
                "choices": [{"message": {"role": "assistant",
                                         "content": "Fake reply."}}]
            })

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
_db_path = os.path.join(_harness.data_dir(), "app.db")
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

import keyring
import keyring.errors

_KR_SERVICE = "chatbot_interface"
_KR_API_KEY = "openrouter_api_key"
_saved_api_key = keyring.get_password(_KR_SERVICE, _KR_API_KEY)

# ── Server setup ─────────────────────────────────────────────────────────────
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

_test_char_ids: list[int] = []
_test_chat_ids: list[int] = []

try:
    # ── Start fake server ─────────────────────────────────────────────────
    fake_server = http.server.HTTPServer(("127.0.0.1", FAKE_PORT), _FakeHandler)
    fake_thread = threading.Thread(target=fake_server.serve_forever, daemon=True)
    fake_thread.start()

    # ── Start backend ─────────────────────────────────────────────────────
    env = _harness.isolated_env()
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

    # The vault shipped after this script was written. The server starts
    # locked by design, so without this every data route answers 423 and
    # each check reports a failure of whatever it is named after instead of
    # the one thing actually wrong. Idempotent, so restarts are safe.
    _harness.open_vault(f"{BASE}/api/v1")

    TEST_KEY = "sk-test-part-a-key"

    # ══════════════════════════════════════════════════════════════════════
    # V-A-1: verify_phase5b.py still passes
    # ══════════════════════════════════════════════════════════════════════
    section("V-A-1  verify_phase5b.py regression")

    # Stop our server first so phase5b can start its own
    server_proc.terminate()
    server_proc.wait(timeout=5)
    server_proc = None
    time.sleep(0.5)

    # Same move, same bug as the aggregate runner: this joined BACKEND_DIR
    # while the script lives in verify/, so V-A-1 launched a path that does
    # not exist and read Python's "cannot open file" exit 2 as a phase5b
    # regression. It reported a failure in the wrong module for months.
    phase5b_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "verify_phase5b.py")
    result_5b = subprocess.run(
        [sys.executable, phase5b_path],
        cwd=BACKEND_DIR,
        timeout=120,
        capture_output=True,
        text=True,
    )
    check("V-A-1  verify_phase5b.py exits 0",
          result_5b.returncode == 0,
          f"exit={result_5b.returncode}")

    # Restart our server for remaining tests
    env = _harness.isolated_env()
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
    started = False
    for _ in range(30):
        time.sleep(0.2)
        try:
            urllib.request.urlopen(f"{BASE}/healthz", timeout=1)
            started = True
            break
        except Exception:
            pass
    assert started, "Backend did not restart"

    # The vault shipped after this script was written. The server starts
    # locked by design, so without this every data route answers 423 and
    # each check reports a failure of whatever it is named after instead of
    # the one thing actually wrong. Idempotent, so restarts are safe.
    _harness.open_vault(f"{BASE}/api/v1")

    # ══════════════════════════════════════════════════════════════════════
    # Setup: create test character + chat
    # ══════════════════════════════════════════════════════════════════════
    section("Setup  Create test character + chat")

    code, char_data = http_post_raw("/api/v1/characters/import",
                                    json.dumps({"name": "PartA Test Char",
                                                "system_prompt": "You are a test.",
                                                "first_mes": "Hello!"}))
    assert code == 201 and char_data, f"Failed: {code}"
    test_char_id = char_data["id"]
    _test_char_ids.append(test_char_id)

    code, chat_data = http_post("/api/v1/chats", {"character_id": test_char_id})
    assert code == 201 and chat_data, f"Failed: {code}"
    test_chat_id = chat_data["id"]
    _test_chat_ids.append(test_chat_id)

    http_post("/api/v1/settings/api-key", {"api_key": TEST_KEY})

    # Fetch models to populate cache
    http_get("/api/v1/models/openrouter")

    # ══════════════════════════════════════════════════════════════════════
    # V-A-2: 402 → openrouter_insufficient_credits
    # ══════════════════════════════════════════════════════════════════════
    section("V-A-2  402 insufficient credits")

    _fake_mode = "credits_402"
    code, body = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "test message",
        "model_id": "openai/gpt-4",
    })
    check("V-A-2  402 → detail=openrouter_insufficient_credits",
          code == 402 and body.get("detail") == "openrouter_insufficient_credits",
          f"code={code} detail={body.get('detail') if body else None}")

    # ══════════════════════════════════════════════════════════════════════
    # V-A-3: 403 → sanitized error code
    # ══════════════════════════════════════════════════════════════════════
    # A 403 is NOT an auth failure. OpenRouter documents it as one thing: the
    # model's moderation refused the input. It used to share auth_failed with
    # 401, which told a user whose message was rejected to go and fix a working
    # API key, so the two are now told apart by the error body's shape.
    section("V-A-3  403 moderation vs unrecognised")

    _fake_mode = "moderation_403"
    code, body = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "test message",
        "model_id": "openai/gpt-4",
    })
    detail = body.get("detail") if body else None
    check("V-A-3a  moderation 403 → 403 openrouter_moderation_blocked",
          code == 403 and detail == "openrouter_moderation_blocked",
          f"code={code} detail={detail}")
    check("V-A-3b  the flagged prompt is not echoed back",
          "RAW_PROMPT_MUST_NOT_LEAK" not in json.dumps(body or {}),
          f"body={body}")

    # A 403 from something that is not OpenRouter (a proxy, a CDN) carries no
    # such metadata. Announcing moderation there would be the same wrong answer
    # pointed the other way, so it stays generic.
    _fake_mode = "auth_403"
    code, body = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
        "message": "test message",
        "model_id": "openai/gpt-4",
    })
    detail = body.get("detail") if body else None
    check("V-A-3c  unrecognised 403 → generic code, not raw body",
          code == 502 and isinstance(detail, str) and len(detail) < 100
          and "forbidden" not in detail.lower(),
          f"code={code} detail={detail}")

    # ══════════════════════════════════════════════════════════════════════
    # V-A-4: Error responses never contain raw upstream body
    # ══════════════════════════════════════════════════════════════════════
    section("V-A-4  No raw upstream body in errors")

    # Already tested above - check that details are code strings
    all_ok = True
    for mode, expected_status in [("credits_402", 402), ("auth_403", 502),
                                  ("moderation_403", 403)]:
        _fake_mode = mode
        code, body = http_post(f"/api/v1/chats/{test_chat_id}/complete", {
            "message": "test msg",
            "model_id": "openai/gpt-4",
        })
        detail = body.get("detail") if body else ""
        # The status was computed and then never looked at, so this loop
        # checked half of what its own name claims.
        if code != expected_status:
            all_ok = False
        if isinstance(detail, str) and ("insufficient credits" in detail
                                         or "forbidden by provider" in detail
                                         or "RAW_PROMPT_MUST_NOT_LEAK" in detail
                                         or "{" in detail):
            all_ok = False

    check("V-A-4  Error detail is a code string, not raw body", all_ok)

    _fake_mode = "normal"

    # ══════════════════════════════════════════════════════════════════════
    # V-A-5: frontend_contract.md exists and has required sections
    # ══════════════════════════════════════════════════════════════════════
    section("V-A-5  frontend_contract.md")

    contract_path = os.path.join(BACKEND_DIR, "..", "docs", "frontend_contract.md")
    contract_exists = os.path.isfile(contract_path)
    check("V-A-5a  docs/frontend_contract.md exists", contract_exists)

    if contract_exists:
        with open(contract_path, encoding="utf-8") as f:
            content = f.read()
        required_sections = ["Endpoint", "Error Code", "Privacy"]
        for sec in required_sections:
            check(f"V-A-5b  contract contains '{sec}' section",
                  sec in content)

finally:
    # ── Cleanup ───────────────────────────────────────────────────────────
    section("Cleanup")

    if server_proc and server_proc.poll() is None:
        server_proc.terminate()
        server_proc.wait(timeout=5)
    if fake_server:
        fake_server.shutdown()

    # Restore keyring
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

    # Clean up test rows
    if _db_existed:
        try:
            with sqlite3.connect(_db_path) as _con:
                all_msg_ids = {
                    r[0] for r in _con.execute("SELECT id FROM messages").fetchall()
                }
                msg_to_del = all_msg_ids - _existing_msg_ids
                if msg_to_del:
                    _con.executemany("DELETE FROM messages WHERE id = ?",
                                    [(i,) for i in msg_to_del])

                all_chat_ids = {
                    r[0] for r in _con.execute("SELECT id FROM chats").fetchall()
                }
                chat_to_del = all_chat_ids - _existing_chat_ids
                if chat_to_del:
                    _con.executemany("DELETE FROM chats WHERE id = ?",
                                    [(i,) for i in chat_to_del])

                all_char_ids = {
                    r[0] for r in _con.execute("SELECT id FROM characters").fetchall()
                }
                char_to_del = all_char_ids - _existing_char_ids
                if char_to_del:
                    _con.executemany("DELETE FROM characters WHERE id = ?",
                                    [(i,) for i in char_to_del])
                _con.commit()
        except Exception:
            pass
    print("  [info] Cleanup done.")

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
