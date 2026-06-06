"""
Phase 4 verification script (Chats Router).
Run from backend/ with the virtual environment active:
    .venv/Scripts/python verify_phase4.py

Safety guarantees:
  - If app.db existed before the run, only test-created rows are deleted.
  - Cleanup order: messages first, chats second, characters last (FK-safe).
  - If app.db did not exist, it is removed after the run.
  - No keyring operations.
  - Message content and first_mes are never printed.
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


def http_post(path: str, data: dict | str | bytes | None = None,
              content_type: str = "application/json") -> tuple[int, dict | None]:
    url = f"{BASE}{path}"
    if isinstance(data, dict):
        body = json.dumps(data).encode()
    elif isinstance(data, str):
        body = data.encode()
    elif isinstance(data, bytes):
        body = data
    else:
        body = b""
    req = urllib.request.Request(url, data=body, method="POST",
                                headers={"Content-Type": content_type})
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


# ── Expected response keys ────────────────────────────────────────────────────
CHAT_KEYS = {
    "id", "character_id", "character_name", "title", "model_id",
    "created_at", "updated_at", "message_count",
}

MSG_KEYS = {"id", "chat_id", "role", "content", "created_at"}


def has_chat_keys(d: dict) -> bool:
    return set(d.keys()) == CHAT_KEYS


def has_msg_keys(d: dict) -> bool:
    return set(d.keys()) == MSG_KEYS


# ── DB helpers ────────────────────────────────────────────────────────────────
_db_path = os.path.join(BACKEND_DIR, "app.db")


def _chat_count() -> int:
    try:
        with sqlite3.connect(_db_path) as con:
            return con.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
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


def _total_msg_count() -> int:
    try:
        with sqlite3.connect(_db_path) as con:
            return con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    except Exception:
        return -1


# ── Setup: DB isolation ──────────────────────────────────────────────────────
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

_test_char_ids: list[int] = []
_test_chat_ids: list[int] = []


# ── Server setup ─────────────────────────────────────────────────────────────
_uvicorn_name = "uvicorn.exe" if sys.platform == "win32" else "uvicorn"
UVICORN_EXE = os.path.join(os.path.dirname(sys.executable), _uvicorn_name)

server_proc: subprocess.Popen | None = None

# ── Port preflight: fail fast if 8787 is already in use ──────────────────────
try:
    urllib.request.urlopen(f"{BASE}/healthz", timeout=1)
    print("  [FATAL] port_8787_already_in_use — another server is running.")
    sys.exit(1)
except Exception:
    pass  # Expected: port is free.

try:
    server_proc = subprocess.Popen(
        [UVICORN_EXE, "main:app",
         "--host", "127.0.0.1", "--port", "8787",
         "--log-level", "warning"],
        cwd=BACKEND_DIR,
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
            continue

    if not started:
        print("  [FATAL] Server did not start in 6 seconds.")
        sys.exit(1)

    # ══════════════════════════════════════════════════════════════════════════
    # V-Route  Route inventory
    # ══════════════════════════════════════════════════════════════════════════
    section("V-Route  Route inventory")

    _, openapi = http_get("/openapi.json")
    paths = openapi.get("paths", {})
    path_keys = sorted(paths.keys())

    phase4_paths = {
        "/api/v1/settings",
        "/api/v1/settings/api-key",
        "/api/v1/settings/proxy",
        "/api/v1/settings/proxy/health",
        "/api/v1/characters",
        "/api/v1/characters/import",
        "/api/v1/characters/{character_id}",
        "/api/v1/chats",
        "/api/v1/chats/{chat_id}",
        "/api/v1/chats/{chat_id}/messages",
    }

    check("V-Route-1  openapi.json contains all 10 Phase 4 path keys",
          phase4_paths <= set(path_keys),
          f"got: {', '.join(path_keys)}")

    # Settings paths still present
    settings_paths = [
        "/api/v1/settings", "/api/v1/settings/api-key",
        "/api/v1/settings/proxy", "/api/v1/settings/proxy/health",
    ]
    check("V-Route-2  all 4 settings paths still present",
          all(p in paths for p in settings_paths))

    # Characters paths still present
    char_paths = [
        "/api/v1/characters", "/api/v1/characters/import",
        "/api/v1/characters/{character_id}",
    ]
    check("V-Route-3  all 3 characters paths still present",
          all(p in paths for p in char_paths))

    # Chat path method checks
    chats_path = paths.get("/api/v1/chats", {})
    chats_methods = sorted(chats_path.keys())
    check("V-Route-4  /chats methods = GET + POST only",
          chats_methods == ["get", "post"],
          f"got: {chats_methods}")

    chat_id_path = paths.get("/api/v1/chats/{chat_id}", {})
    chat_id_methods = sorted(chat_id_path.keys())
    check("V-Route-5  /chats/{chat_id} methods = GET only",
          chat_id_methods == ["get"],
          f"got: {chat_id_methods}")

    msgs_path = paths.get("/api/v1/chats/{chat_id}/messages", {})
    msgs_methods = sorted(msgs_path.keys())
    check("V-Route-6  /chats/{chat_id}/messages methods = GET only",
          msgs_methods == ["get"],
          f"got: {msgs_methods}")

    # No PUT/PATCH/DELETE on any chat path
    all_chat_methods = set()
    for pk in ["/api/v1/chats", "/api/v1/chats/{chat_id}",
               "/api/v1/chats/{chat_id}/messages"]:
        all_chat_methods.update(paths.get(pk, {}).keys())
    forbidden_methods = all_chat_methods & {"put", "patch", "delete"}
    check("V-Route-7  no PUT/PATCH/DELETE on chat paths",
          len(forbidden_methods) == 0,
          f"forbidden: {forbidden_methods}" if forbidden_methods else "")

    # No POST on messages or complete
    check("V-Route-8  no POST /chats/{chat_id}/messages",
          "post" not in msgs_methods)

    check("V-Route-9a  /api/v1/chats/{chat_id}/complete registered (Phase 5B+)",
          "/api/v1/chats/{chat_id}/complete" in path_keys)
    check("V-Route-9b  /api/v1/completions NOT registered",
          "/api/v1/completions" not in path_keys)

    # No double prefix
    check("V-Route-10  no /api/v1/api/v1 double prefix",
          not any("/api/v1/api/v1" in p for p in path_keys))

    # Phase 5A+ coexistence: models may be registered
    check("V-Route-11  /api/v1/models/openrouter registered (Phase 5A+)",
          any("/models/openrouter" in p for p in path_keys))

    check("V-Route-12  /api/v1/completions NOT registered",
          not any("completions" in p for p in path_keys))

    # /healthz
    code, body = http_get("/healthz")
    check("V-Route-13  /healthz responds 200 + ok=true",
          code == 200 and body == {"ok": True},
          f"code={code}")

    # ══════════════════════════════════════════════════════════════════════════
    # V-Create  Chat creation
    # ══════════════════════════════════════════════════════════════════════════
    section("V-Create  Chat creation")

    # --- Create a test character WITH first_mes ---
    code, char_data = http_post("/api/v1/characters", {
        "name": "Chat Test Char",
        "description": "test",
        "tags": ["test"],
    })
    char_with_no_fmes_id = char_data.get("id") if char_data else None
    if char_with_no_fmes_id:
        _test_char_ids.append(char_with_no_fmes_id)

    # Create character with first_mes via import (manual create doesn't set first_mes)
    _expected_first_mes = "Hello! I am a character."
    import_payload = {
        "name": "Chat FirstMes Char",
        "first_mes": _expected_first_mes,
        "description": "imported for chat test",
    }
    code, fmes_char = http_post("/api/v1/characters/import",
                                json.dumps(import_payload))
    char_with_fmes_id = fmes_char.get("id") if fmes_char else None
    if char_with_fmes_id:
        _test_char_ids.append(char_with_fmes_id)

    # --- POST /chats with character that has first_mes ---
    code, chat1 = http_post("/api/v1/chats", {
        "character_id": char_with_fmes_id,
    })
    check("V-Create-1  POST /chats -> 201", code == 201, f"code={code}")

    chat1_id = chat1.get("id") if chat1 else None
    if chat1_id:
        _test_chat_ids.append(chat1_id)

    check("V-Create-2  response has exactly 8 chat keys",
          chat1 is not None and has_chat_keys(chat1),
          f"keys={sorted(chat1.keys()) if chat1 else 'None'}")

    check("V-Create-3  character_name populated",
          chat1 is not None and chat1.get("character_name") == "Chat FirstMes Char",
          f"got={chat1.get('character_name') if chat1 else 'None'}")

    # Default title includes character name
    _title1 = chat1.get("title", "") if chat1 else ""
    check("V-Create-4  default title includes character name",
          "Chat FirstMes Char" in _title1,
          f"title={_title1!r}")

    check("V-Create-5  model_id is null when not provided",
          chat1 is not None and chat1.get("model_id") is None,
          f"got={chat1.get('model_id') if chat1 else '?'}")

    check("V-Create-6  created_at is string",
          chat1 is not None and isinstance(chat1.get("created_at"), str))

    check("V-Create-7  updated_at is string",
          chat1 is not None and isinstance(chat1.get("updated_at"), str))

    check("V-Create-8  message_count=1 (first_mes inserted)",
          chat1 is not None and chat1.get("message_count") == 1,
          f"got={chat1.get('message_count') if chat1 else '?'}")

    # --- Verify first_mes created an assistant message ---
    if chat1_id:
        code, msgs = http_get(f"/api/v1/chats/{chat1_id}/messages")
        check("V-Create-9  GET messages returns 1 message",
              code == 200 and isinstance(msgs, list) and len(msgs) == 1,
              f"code={code}, count={len(msgs) if isinstance(msgs, list) else '?'}")

        if msgs and len(msgs) == 1:
            msg0 = msgs[0]
            check("V-Create-10  first_mes role is 'assistant'",
                  msg0.get("role") == "assistant",
                  f"role={msg0.get('role')}")
            check("V-Create-10b  message has exactly 5 keys",
                  has_msg_keys(msg0),
                  f"keys={sorted(msg0.keys())}")
            check("V-Create-10c  first_mes content matches expected",
                  msg0.get("content") == _expected_first_mes)
        else:
            check("V-Create-10  first_mes role is 'assistant'", False, "no messages")
            check("V-Create-10b  message has exactly 5 keys", False, "no messages")
            check("V-Create-10c  first_mes content matches expected", False, "no messages")
    else:
        check("V-Create-9  GET messages returns 1 message", False, "no chat1_id")
        check("V-Create-10  first_mes role is 'assistant'", False, "no chat1_id")
        check("V-Create-10b  message has exactly 5 keys", False, "no chat1_id")
        check("V-Create-10c  first_mes content matches expected", False, "no chat1_id")

    # --- POST /chats with character that has empty first_mes ---
    code, chat2 = http_post("/api/v1/chats", {
        "character_id": char_with_no_fmes_id,
    })
    chat2_id = chat2.get("id") if chat2 else None
    if chat2_id:
        _test_chat_ids.append(chat2_id)

    check("V-Create-11  chat with empty first_mes -> 201",
          code == 201, f"code={code}")

    check("V-Create-12  message_count=0 (no first_mes)",
          chat2 is not None and chat2.get("message_count") == 0,
          f"got={chat2.get('message_count') if chat2 else '?'}")

    # --- Custom title and model_id ---
    code, chat3 = http_post("/api/v1/chats", {
        "character_id": char_with_no_fmes_id,
        "title": "  Custom Title  ",
        "model_id": "  openrouter/model-v1  ",
    })
    chat3_id = chat3.get("id") if chat3 else None
    if chat3_id:
        _test_chat_ids.append(chat3_id)

    _title3 = repr(chat3.get('title')) if chat3 else '?'
    check("V-Create-13  custom title is stripped",
          chat3 is not None and chat3.get("title") == "Custom Title",
          f"got={_title3}")

    _mid3 = repr(chat3.get('model_id')) if chat3 else '?'
    check("V-Create-14  model_id is stripped",
          chat3 is not None and chat3.get("model_id") == "openrouter/model-v1",
          f"got={_mid3}")

    # --- model_id empty/whitespace -> null ---
    code, chat4 = http_post("/api/v1/chats", {
        "character_id": char_with_no_fmes_id,
        "model_id": "   ",
    })
    chat4_id = chat4.get("id") if chat4 else None
    if chat4_id:
        _test_chat_ids.append(chat4_id)

    _mid4 = repr(chat4.get('model_id')) if chat4 else '?'
    check("V-Create-15  model_id='   ' -> null",
          chat4 is not None and chat4.get("model_id") is None,
          f"got={_mid4}")

    # --- Unknown character_id -> 404 ---
    before_chats = _chat_count()
    before_msgs_404 = _total_msg_count()
    code, err_data = http_post("/api/v1/chats", {
        "character_id": 999999,
    })
    check("V-Create-16  unknown character_id -> 404 character_not_found",
          code == 404 and err_data is not None
          and err_data.get("detail") == "character_not_found",
          f"code={code}")
    check("V-Create-17  no chat row created on 404",
          _chat_count() == before_chats,
          f"before={before_chats}, after={_chat_count()}")
    check("V-Create-17b  no message row created on 404",
          _total_msg_count() == before_msgs_404,
          f"before={before_msgs_404}, after={_total_msg_count()}")

    # --- Missing character_id body -> 422 ---
    before_chats2 = _chat_count()
    before_msgs = _total_msg_count()
    code, err_body = http_post("/api/v1/chats", {})
    check("V-Create-18  POST /chats {} -> 422",
          code == 422, f"code={code}")
    check("V-Create-19  no chat row created on 422",
          _chat_count() == before_chats2,
          f"before={before_chats2}, after={_chat_count()}")
    check("V-Create-20  no message row created on 422",
          _total_msg_count() == before_msgs,
          f"before={before_msgs}, after={_total_msg_count()}")

    # --- Whitespace-only title -> default title ---
    code, chat_ws = http_post("/api/v1/chats", {
        "character_id": char_with_no_fmes_id,
        "title": "   ",
    })
    chat_ws_id = chat_ws.get("id") if chat_ws else None
    if chat_ws_id:
        _test_chat_ids.append(chat_ws_id)
    _ws_title = chat_ws.get("title", "") if chat_ws else ""
    check("V-Create-21  whitespace title -> 201",
          code == 201, f"code={code}")
    check("V-Create-22  whitespace title uses default (includes char name)",
          chat_ws is not None and "Chat Test Char" in _ws_title
          and _ws_title != "",
          f"title={_ws_title!r}")

    # ══════════════════════════════════════════════════════════════════════════
    # V-List  List chats
    # ══════════════════════════════════════════════════════════════════════════
    section("V-List  List chats")

    code, chats_list = http_get("/api/v1/chats")
    check("V-List-1  GET /chats returns array",
          code == 200 and isinstance(chats_list, list),
          f"code={code}, type={type(chats_list).__name__}")

    # Created chats should appear
    created_ids = {chat1_id, chat2_id, chat3_id, chat4_id} - {None}
    found_ids = {c.get("id") for c in (chats_list or [])}
    check("V-List-2  all test chats appear in list",
          created_ids <= found_ids,
          f"missing={created_ids - found_ids}")

    # Exact 8 keys per item
    all_8_keys = all(has_chat_keys(c) for c in (chats_list or []))
    check("V-List-3  each list item has exactly 8 chat keys", all_8_keys)

    # No raw_json
    no_raw = all("raw_json" not in c for c in (chats_list or []))
    check("V-List-4  no item in list has raw_json", no_raw)

    # Ordering: ids should be non-increasing (updated_at DESC, id DESC)
    if isinstance(chats_list, list) and len(chats_list) >= 2:
        list_ids = [c.get("id") for c in chats_list]
        list_updated = [c.get("updated_at", "") for c in chats_list]
        # updated_at DESC: each should be >= the next (string comparison ok for
        # ISO timestamps). When equal, id DESC.
        ordering_ok = True
        for i in range(len(chats_list) - 1):
            ua_a = list_updated[i]
            ua_b = list_updated[i + 1]
            if ua_a < ua_b:
                ordering_ok = False
                break
            if ua_a == ua_b and list_ids[i] < list_ids[i + 1]:
                ordering_ok = False
                break
        check("V-List-5  ordering is updated_at DESC, id DESC",
              ordering_ok,
              f"ids={list_ids}")
    else:
        check("V-List-5  ordering is updated_at DESC, id DESC",
              True, "not enough items to verify")

    # ══════════════════════════════════════════════════════════════════════════
    # V-Get  Get chat by ID
    # ══════════════════════════════════════════════════════════════════════════
    section("V-Get  Get chat by ID")

    if chat1_id:
        code, data = http_get(f"/api/v1/chats/{chat1_id}")
        check("V-Get-1  GET /chats/{id} -> 200, correct chat",
              code == 200 and data is not None and data.get("id") == chat1_id,
              f"code={code}")

        check("V-Get-2  response has exactly 8 chat keys",
              data is not None and has_chat_keys(data),
              f"keys={sorted(data.keys()) if data else 'None'}")

        check("V-Get-3  message_count matches",
              data is not None and data.get("message_count") == 1)
    else:
        check("V-Get-1  GET /chats/{id} -> 200", False, "no chat1_id")
        check("V-Get-2  response has exactly 8 chat keys", False, "no chat1_id")
        check("V-Get-3  message_count matches", False, "no chat1_id")

    code, data = http_get("/api/v1/chats/999999")
    check("V-Get-4  GET /chats/999999 -> 404 chat_not_found",
          code == 404 and data is not None
          and data.get("detail") == "chat_not_found",
          f"code={code}")

    # ══════════════════════════════════════════════════════════════════════════
    # V-Messages  Messages endpoint
    # ══════════════════════════════════════════════════════════════════════════
    section("V-Messages  Messages endpoint")

    # 404 for unknown chat
    code, data = http_get("/api/v1/chats/999999/messages")
    check("V-Msg-1  GET /chats/999999/messages -> 404 chat_not_found",
          code == 404 and data is not None
          and data.get("detail") == "chat_not_found",
          f"code={code}")

    # Empty chat has 0 messages
    if chat2_id:
        code, msgs = http_get(f"/api/v1/chats/{chat2_id}/messages")
        check("V-Msg-2  empty chat returns [] messages",
              code == 200 and isinstance(msgs, list) and len(msgs) == 0,
              f"count={len(msgs) if isinstance(msgs, list) else '?'}")
    else:
        check("V-Msg-2  empty chat returns [] messages", False, "no chat2_id")

    # Message ordering by id ASC — manually insert extra messages
    if chat1_id:
        try:
            with sqlite3.connect(_db_path) as _con:
                _con.execute(
                    "INSERT INTO messages (chat_id, role, content) "
                    "VALUES (?, 'user', 'test user msg')",
                    (chat1_id,),
                )
                _con.execute(
                    "INSERT INTO messages (chat_id, role, content) "
                    "VALUES (?, 'assistant', 'test assistant msg')",
                    (chat1_id,),
                )
                _con.commit()
        except Exception:
            pass

        code, msgs = http_get(f"/api/v1/chats/{chat1_id}/messages")
        check("V-Msg-3  messages returned after manual insert",
              code == 200 and isinstance(msgs, list) and len(msgs) == 3,
              f"count={len(msgs) if isinstance(msgs, list) else '?'}")

        if isinstance(msgs, list) and len(msgs) >= 3:
            ids = [m.get("id") for m in msgs]
            check("V-Msg-4  message ids are ascending",
                  ids == sorted(ids),
                  f"ids={ids}")

            roles = [m.get("role") for m in msgs]
            check("V-Msg-5  roles are [assistant, user, assistant]",
                  roles == ["assistant", "user", "assistant"],
                  f"roles={roles}")

            all_msg_keys = all(has_msg_keys(m) for m in msgs)
            check("V-Msg-6  each message has exactly 5 keys", all_msg_keys)
        else:
            check("V-Msg-4  message ids are ascending", False, "not enough msgs")
            check("V-Msg-5  roles are [assistant, user, assistant]", False,
                  "not enough msgs")
            check("V-Msg-6  each message has exactly 5 keys", False,
                  "not enough msgs")
    else:
        for i in range(3, 7):
            check(f"V-Msg-{i}  (skipped, no chat1_id)", False, "no chat1_id")

    # ══════════════════════════════════════════════════════════════════════════
    # V-Count  message_count correctness
    # ══════════════════════════════════════════════════════════════════════════
    section("V-Count  message_count correctness")

    # After manual inserts, GET /chats/{chat1_id} should show message_count=3
    if chat1_id:
        code, data = http_get(f"/api/v1/chats/{chat1_id}")
        check("V-Count-1  message_count=3 after manual inserts",
              data is not None and data.get("message_count") == 3,
              f"got={data.get('message_count') if data else '?'}")
    else:
        check("V-Count-1  message_count=3 after manual inserts", False,
              "no chat1_id")

    # In list endpoint, verify counts
    code, chats_list2 = http_get("/api/v1/chats")
    if isinstance(chats_list2, list) and chat1_id and chat2_id:
        c1 = next((c for c in chats_list2 if c.get("id") == chat1_id), None)
        c2 = next((c for c in chats_list2 if c.get("id") == chat2_id), None)
        check("V-Count-2  list: chat1 message_count=3",
              c1 is not None and c1.get("message_count") == 3,
              f"got={c1.get('message_count') if c1 else '?'}")
        check("V-Count-3  list: chat2 message_count=0",
              c2 is not None and c2.get("message_count") == 0,
              f"got={c2.get('message_count') if c2 else '?'}")
    else:
        check("V-Count-2  list: chat1 message_count=3", False, "missing data")
        check("V-Count-3  list: chat2 message_count=0", False, "missing data")

    # ══════════════════════════════════════════════════════════════════════════
    # V-DB  Direct SQLite checks
    # ══════════════════════════════════════════════════════════════════════════
    section("V-DB  SQLite storage checks")

    if chat1_id:
        try:
            with sqlite3.connect(_db_path) as _con:
                _con.row_factory = sqlite3.Row
                msg_rows = _con.execute(
                    "SELECT id FROM messages WHERE chat_id = ? ORDER BY id ASC",
                    (chat1_id,),
                ).fetchall()
                db_ids = [r["id"] for r in msg_rows]
            check("V-DB-1  DB messages ORDER BY id ASC is correct",
                  db_ids == sorted(db_ids),
                  f"ids={db_ids}")
        except Exception as ex:
            check("V-DB-1  DB messages ORDER BY id ASC is correct", False,
                  str(ex))

        try:
            with sqlite3.connect(_db_path) as _con:
                _con.row_factory = sqlite3.Row
                chat_row = _con.execute(
                    "SELECT updated_at FROM chats WHERE id = ?", (chat1_id,),
                ).fetchone()
            check("V-DB-2  updated_at is string",
                  chat_row is not None
                  and isinstance(chat_row["updated_at"], str),
                  f"type={type(chat_row['updated_at']).__name__ if chat_row else '?'}")
        except Exception as ex:
            check("V-DB-2  updated_at is string", False, str(ex))

        # message_count reflects DB reality
        actual_db_count = _msg_count_for(chat1_id)
        check("V-DB-3  message_count reflects DB message rows",
              actual_db_count == 3,
              f"db_count={actual_db_count}")
    else:
        check("V-DB-1  DB messages ORDER BY id ASC", False, "no chat1_id")
        check("V-DB-2  updated_at is string", False, "no chat1_id")
        check("V-DB-3  message_count reflects DB", False, "no chat1_id")

    # ══════════════════════════════════════════════════════════════════════════
    # V-Src  Static source analysis
    # ══════════════════════════════════════════════════════════════════════════
    section("V-Src  Static source analysis")

    chats_src_path = os.path.join(BACKEND_DIR, "routers", "chats.py")
    with open(chats_src_path, "r", encoding="utf-8") as f:
        chats_src = f.read()

    # Extract code lines only (skip comments and docstrings)
    _code_lines = []
    _in_docstring = False
    for line in chats_src.splitlines():
        stripped = line.strip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            if stripped.count('"""') == 1 or stripped.count("'''") == 1:
                _in_docstring = not _in_docstring
            continue
        if _in_docstring:
            continue
        if stripped.startswith("#"):
            continue
        _code_lines.append(stripped)
    _code_text = "\n".join(_code_lines)

    forbidden = {
        "V-Src-1": "httpx",
        "V-Src-2": "requests",
        "V-Src-3": "urllib.request",
        "V-Src-4": "keyring",
        "V-Src-5": "openrouter",
        "V-Src-6": "network_client",
        "V-Src-7": "proxy_health",
    }
    for vid, keyword in forbidden.items():
        check(f"{vid}  chats.py code has no '{keyword}'",
              keyword not in _code_text)

    # main.py checks — strip comments/docstrings
    main_src_path = os.path.join(BACKEND_DIR, "main.py")
    with open(main_src_path, "r", encoding="utf-8") as f:
        main_raw = f.read()

    _main_code_lines = []
    _main_in_docstring = False
    for line in main_raw.splitlines():
        stripped = line.strip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            if stripped.count('"""') == 1 or stripped.count("'''") == 1:
                _main_in_docstring = not _main_in_docstring
            continue
        if _main_in_docstring:
            continue
        if stripped.startswith("#"):
            continue
        _main_code_lines.append(stripped)
    main_code = "\n".join(_main_code_lines)

    check("V-Src-8  main.py has active settings router import",
          "from routers import settings" in main_code)

    check("V-Src-9  main.py has active characters router import",
          "from routers import characters" in main_code)

    check("V-Src-10  main.py has active chats router import",
          "from routers import chats" in main_code)

    # Phase 5A+: models_router is active, completions must not be
    check("V-Src-11  main.py has active models_router import (Phase 5A+)",
          "from routers import models_router" in main_code)

    check("V-Src-12  main.py has active completions router (Phase 5B+)",
          "from routers import completions" in main_code)

except FileNotFoundError as e:
    print(f"  [FATAL] {e}")
except Exception as e:
    import traceback
    traceback.print_exc()

finally:
    # ── Stop server ───────────────────────────────────────────────────────────
    if server_proc is not None:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_proc.kill()
            server_proc.wait()

    # ── DB cleanup (FK-safe order: messages → chats → characters) ─────────
    time.sleep(0.3)
    if _db_existed:
        try:
            with sqlite3.connect(_db_path) as _con:
                # Messages first
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

                # Chats second
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

                # Characters last
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
        print(f"  [info] {_total} test rows removed from existing app.db "
              f"(msgs+chats+chars).")
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
