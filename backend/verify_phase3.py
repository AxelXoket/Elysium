"""
Phase 3 verification script (Characters Router).
Run from backend/ with the virtual environment active:
    .venv/Scripts/python verify_phase3.py

Safety guarantees:
  - If app.db existed before the run, only test-created character rows are deleted.
  - If app.db did not exist, it is removed after the run.
  - No keyring operations.
  - raw_json content is never printed.
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
EXPECTED_KEYS = {
    "id", "name", "description", "personality", "scenario",
    "first_mes", "mes_example", "system_prompt",
    "post_history_instruction", "tags", "created_at",
}


def has_exact_keys(d: dict) -> bool:
    return set(d.keys()) == EXPECTED_KEYS


# ── DB helpers ────────────────────────────────────────────────────────────────
_db_path = os.path.join(BACKEND_DIR, "app.db")


def _char_count() -> int:
    try:
        with sqlite3.connect(_db_path) as con:
            return con.execute("SELECT COUNT(*) FROM characters").fetchone()[0]
    except Exception:
        return -1


def _get_raw_json(char_id: int) -> str:
    try:
        with sqlite3.connect(_db_path) as con:
            con.row_factory = sqlite3.Row
            row = con.execute(
                "SELECT raw_json FROM characters WHERE id = ?", (char_id,)
            ).fetchone()
        return row["raw_json"] if row else ""
    except Exception:
        return ""


def _get_tags_raw(char_id: int) -> str:
    try:
        with sqlite3.connect(_db_path) as con:
            con.row_factory = sqlite3.Row
            row = con.execute(
                "SELECT tags FROM characters WHERE id = ?", (char_id,)
            ).fetchone()
        return row["tags"] if row else ""
    except Exception:
        return ""


# ── Setup: DB isolation ──────────────────────────────────────────────────────
_db_existed = os.path.exists(_db_path)
_existing_char_ids: set[int] = set()
if _db_existed:
    try:
        with sqlite3.connect(_db_path) as _con:
            _existing_char_ids = {
                r[0] for r in _con.execute("SELECT id FROM characters").fetchall()
            }
    except Exception:
        pass

_test_char_ids: list[int] = []


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

    expected_paths = sorted([
        "/api/v1/settings",
        "/api/v1/settings/api-key",
        "/api/v1/settings/proxy",
        "/api/v1/settings/proxy/health",
        "/api/v1/characters",
        "/api/v1/characters/import",
        "/api/v1/characters/{character_id}",
    ])

    check("V-Route-1  openapi.json contains all 7 Phase 3 path keys",
          set(expected_paths) <= set(path_keys),
          f"got: {', '.join(path_keys)}")

    # Phase 2 settings paths still present
    settings_paths = [
        "/api/v1/settings",
        "/api/v1/settings/api-key",
        "/api/v1/settings/proxy",
        "/api/v1/settings/proxy/health",
    ]
    check("V-Route-2  all 4 settings paths still present",
          all(p in paths for p in settings_paths))

    # Method checks for character paths
    chars_path = paths.get("/api/v1/characters", {})
    chars_methods = sorted(chars_path.keys())
    check("V-Route-3  /characters methods = GET + POST only",
          chars_methods == ["get", "post"],
          f"got: {chars_methods}")

    import_path = paths.get("/api/v1/characters/import", {})
    import_methods = sorted(import_path.keys())
    check("V-Route-4  /characters/import methods = POST only",
          import_methods == ["post"],
          f"got: {import_methods}")

    char_id_path = paths.get("/api/v1/characters/{character_id}", {})
    char_id_methods = sorted(char_id_path.keys())
    check("V-Route-5  /characters/{character_id} methods = GET only",
          char_id_methods == ["get"],
          f"got: {char_id_methods}")

    # No PUT/PATCH/DELETE on any character path
    all_char_methods = set()
    for pk in ["/api/v1/characters", "/api/v1/characters/import",
               "/api/v1/characters/{character_id}"]:
        all_char_methods.update(paths.get(pk, {}).keys())
    forbidden_methods = all_char_methods & {"put", "patch", "delete"}
    check("V-Route-6  no PUT/PATCH/DELETE on character paths",
          len(forbidden_methods) == 0,
          f"forbidden found: {forbidden_methods}" if forbidden_methods else "")

    # No double prefix
    check("V-Route-7  no /api/v1/api/v1 double prefix",
          not any("/api/v1/api/v1" in p for p in path_keys),
          f"paths: {', '.join(path_keys)}")

    # Future routes not registered
    check("V-Route-8  /api/v1/chats registered (Phase 4+)",
          any("chats" in p for p in path_keys))
    check("V-Route-9  /api/v1/models/openrouter registered (Phase 5A+)",
          any("/models/openrouter" in p for p in path_keys))
    check("V-Route-10a  /api/v1/chats/{chat_id}/complete registered (Phase 5B+)",
          "/api/v1/chats/{chat_id}/complete" in path_keys)
    check("V-Route-10b  /api/v1/completions NOT registered",
          "/api/v1/completions" not in path_keys)

    # /healthz
    code, body = http_get("/healthz")
    check("V-Route-11  /healthz responds 200 + ok=true",
          code == 200 and body == {"ok": True},
          f"code={code}")

    # ══════════════════════════════════════════════════════════════════════════
    # V-Create  Manual create
    # ══════════════════════════════════════════════════════════════════════════
    section("V-Create  Manual create")

    code, data = http_post("/api/v1/characters", {
        "name": "  Test Char  ",
        "description": "test desc",
        "tags": ["tag1", "tag2"],
    })
    check("V-Create-1  POST /characters -> 201", code == 201, f"code={code}")

    created_id = data.get("id") if data else None
    if created_id:
        _test_char_ids.append(created_id)

    _keys_str = str(sorted(data.keys())) if data else "None"
    check("V-Create-2  response has exactly 11 keys, no raw_json",
          data is not None and has_exact_keys(data),
          f"keys={_keys_str}")

    _tags_type = type(data.get("tags")).__name__ if data else "None"
    check("V-Create-3  tags returned as array",
          data is not None and isinstance(data.get("tags"), list),
          f"type={_tags_type}")

    _name_val = repr(data.get("name")) if data else "None"
    check("V-Create-4  stripped name stored correctly",
          data is not None and data.get("name") == "Test Char",
          f"got={_name_val}")

    check("V-Create-4b  created_at is string",
          data is not None and isinstance(data.get("created_at"), str),
          f"type={type(data.get('created_at')).__name__ if data else 'None'}")

    # Invalid creates — name empty
    before = _char_count()
    code, _ = http_post("/api/v1/characters", {"name": ""})
    check("V-Create-5  name='' -> 422, no DB row",
          code == 422 and _char_count() == before,
          f"code={code}")

    before = _char_count()
    code, _ = http_post("/api/v1/characters", {"name": "   "})
    check("V-Create-6  name='   ' -> 422, no DB row",
          code == 422 and _char_count() == before,
          f"code={code}")

    # Invalid tags
    before = _char_count()
    code, _ = http_post("/api/v1/characters", {"name": "X", "tags": None})
    check("V-Create-7  tags=null -> 422, no DB row",
          code == 422 and _char_count() == before,
          f"code={code}")

    before = _char_count()
    code, _ = http_post("/api/v1/characters", {"name": "X", "tags": "tag"})
    check("V-Create-8  tags='tag' -> 422, no DB row",
          code == 422 and _char_count() == before,
          f"code={code}")

    before = _char_count()
    code, _ = http_post("/api/v1/characters", {"name": "X", "tags": [1, 2]})
    check("V-Create-9  tags=[1,2] -> 422, no DB row",
          code == 422 and _char_count() == before,
          f"code={code}")

    before = _char_count()
    code, _ = http_post("/api/v1/characters", {"name": "X", "tags": ["ok", 1]})
    check("V-Create-10  tags=['ok',1] -> 422, no DB row",
          code == 422 and _char_count() == before,
          f"code={code}")

    # ══════════════════════════════════════════════════════════════════════════
    # V-List  List characters
    # ══════════════════════════════════════════════════════════════════════════
    section("V-List  List characters")

    code, chars = http_get("/api/v1/characters")
    check("V-List-1  GET /characters returns array",
          code == 200 and isinstance(chars, list),
          f"code={code}, type={type(chars).__name__}")

    found_test = any(c.get("id") == created_id for c in (chars or []))
    check("V-List-2  created character appears in list", found_test)

    no_raw_in_list = all("raw_json" not in c for c in (chars or []))
    check("V-List-3  no item in list has raw_json key", no_raw_in_list)

    all_11_keys = all(has_exact_keys(c) for c in (chars or []))
    check("V-List-4  each list item has exactly 11 keys", all_11_keys)

    # ══════════════════════════════════════════════════════════════════════════
    # V-Get  Get character by ID
    # ══════════════════════════════════════════════════════════════════════════
    section("V-Get  Get character by ID")

    code, data = http_get(f"/api/v1/characters/{created_id}")
    check("V-Get-1  GET /characters/{id} -> 200, correct character",
          code == 200 and data is not None and data.get("id") == created_id,
          f"code={code}")

    _get_keys = str(sorted(data.keys())) if data else "None"
    check("V-Get-2  response has exactly 11 keys, no raw_json",
          data is not None and has_exact_keys(data),
          f"keys={_get_keys}")

    check("V-Get-2b  created_at is string",
          data is not None and isinstance(data.get("created_at"), str),
          f"type={type(data.get('created_at')).__name__ if data else 'None'}")

    code, data = http_get("/api/v1/characters/999999")
    check("V-Get-3  GET /characters/999999 -> 404 character_not_found",
          code == 404 and data is not None and data.get("detail") == "character_not_found",
          f"code={code}")

    # ══════════════════════════════════════════════════════════════════════════
    # V-DB  Direct SQLite checks
    # ══════════════════════════════════════════════════════════════════════════
    section("V-DB  SQLite storage checks")

    if created_id:
        tags_raw = _get_tags_raw(created_id)
        check("V-DB-1  tags stored as JSON text string",
              isinstance(tags_raw, str) and tags_raw.startswith("["),
              f"type={type(tags_raw).__name__}")

        raw_json_val = _get_raw_json(created_id)
        check("V-DB-2  manual create raw_json = '{}'",
              raw_json_val == "{}",
              "correct" if raw_json_val == "{}" else "mismatch")
    else:
        check("V-DB-1  tags stored as JSON text string", False, "no created_id")
        check("V-DB-2  manual create raw_json = '{}'", False, "no created_id")

    # ══════════════════════════════════════════════════════════════════════════
    # V-Import  Import tests
    # ══════════════════════════════════════════════════════════════════════════
    section("V-Import  Import character")

    # --- Direct JSON import ---
    import_payload = {
        "name": "Imported Char",
        "description": "imported desc",
        "personality": "friendly",
        "unknown_field": "preserved",
        "character_book": {"entries": []},
        "extensions": {"ext1": True},
        "alternate_greetings": ["Hi!", "Hello!"],
    }
    code, data = http_post("/api/v1/characters/import",
                           json.dumps(import_payload))
    check("V-Import-1  direct JSON import -> 201", code == 201, f"code={code}")

    imported_id = data.get("id") if data else None
    if imported_id:
        _test_char_ids.append(imported_id)

    _imp_keys = str(sorted(data.keys())) if data else "None"
    check("V-Import-2  import response exactly 11 keys, no raw_json",
          data is not None and has_exact_keys(data),
          f"keys={_imp_keys}")

    # --- CharCard V2 wrapper ---
    v2_payload = {
        "spec": "chara_card_v2",
        "data": {
            "name": "V2 Char",
            "description": "v2 desc",
            "scenario": "v2 scenario",
        }
    }
    code, data = http_post("/api/v1/characters/import",
                           json.dumps(v2_payload))
    check("V-Import-3  CharCard V2 wrapper import -> 201",
          code == 201, f"code={code}")
    v2_id = data.get("id") if data else None
    if v2_id:
        _test_char_ids.append(v2_id)

    check("V-Import-3b  V2 response has exactly 11 keys",
          data is not None and has_exact_keys(data),
          f"keys={str(sorted(data.keys())) if data else 'None'}")

    check("V-Import-3c  V2 created_at is string",
          data is not None and isinstance(data.get("created_at"), str),
          f"type={type(data.get('created_at')).__name__ if data else 'None'}")

    # --- Missing fields default to "" ---
    minimal = {"name": "Minimal"}
    code, data = http_post("/api/v1/characters/import",
                           json.dumps(minimal))
    minimal_id = data.get("id") if data else None
    if minimal_id:
        _test_char_ids.append(minimal_id)

    check("V-Import-4b  minimal response has exactly 11 keys",
          data is not None and has_exact_keys(data),
          f"keys={str(sorted(data.keys())) if data else 'None'}")

    _desc_val = repr(data.get("description")) if data else "?"
    check("V-Import-4  missing fields default to ''",
          data is not None
          and data.get("description") == ""
          and data.get("personality") == ""
          and data.get("scenario") == "",
          f"desc={_desc_val}")

    # --- post_history_instructions plural alias ---
    plural_payload = {
        "name": "Plural PHI",
        "post_history_instructions": "plural value",
    }
    code, data = http_post("/api/v1/characters/import",
                           json.dumps(plural_payload))
    plural_id = data.get("id") if data else None
    if plural_id:
        _test_char_ids.append(plural_id)
    _phi_val = repr(data.get("post_history_instruction")) if data else "?"
    check("V-Import-5  post_history_instructions plural -> post_history_instruction",
          data is not None
          and data.get("post_history_instruction") == "plural value",
          f"got={_phi_val}")

    # --- Singular wins when both exist ---
    both_payload = {
        "name": "Both PHI",
        "post_history_instruction": "singular wins",
        "post_history_instructions": "plural loses",
    }
    code, data = http_post("/api/v1/characters/import",
                           json.dumps(both_payload))
    both_id = data.get("id") if data else None
    if both_id:
        _test_char_ids.append(both_id)
    _phi_val2 = repr(data.get("post_history_instruction")) if data else "?"
    check("V-Import-6  singular wins when both present",
          data is not None
          and data.get("post_history_instruction") == "singular wins",
          f"got={_phi_val2}")

    # --- raw_json preservation checks (key presence only, never print value) ---
    if imported_id:
        raw = _get_raw_json(imported_id)
        try:
            raw_obj = json.loads(raw)
        except Exception:
            raw_obj = {}
        check("V-Import-7  unknown_field in raw_json",
              "unknown_field" in raw_obj)
        check("V-Import-8  character_book in raw_json",
              "character_book" in raw_obj)
        check("V-Import-9  extensions in raw_json",
              "extensions" in raw_obj)
        check("V-Import-10  alternate_greetings in raw_json",
              "alternate_greetings" in raw_obj)
    else:
        for i in range(7, 11):
            check(f"V-Import-{i}  raw_json preservation", False, "no imported_id")

    # raw_json NOT in response
    check("V-Import-11  raw_json NOT in import response",
          data is not None and "raw_json" not in data)

    # V-DB-3 — import raw_json is not '{}'
    if imported_id:
        raw_val = _get_raw_json(imported_id)
        try:
            raw_parsed = json.loads(raw_val)
        except Exception:
            raw_parsed = None
        check("V-DB-3  import raw_json stored (not empty)",
              isinstance(raw_parsed, dict) and len(raw_parsed) > 0,
              "has content" if raw_parsed else "empty or invalid")
    else:
        check("V-DB-3  import raw_json stored (not empty)", False, "no imported_id")

    # --- Invalid import: oversized body ---
    before = _char_count()
    oversized = b'{"name": "X", "data": "' + b"A" * (1_048_577) + b'"}'
    code, data = http_post("/api/v1/characters/import", oversized)
    check("V-Import-12  body > 1 MiB -> 400 character_json_too_large, no row",
          code == 400 and data.get("detail") == "character_json_too_large"
          and _char_count() == before,
          f"code={code}")

    # --- Invalid import: bad JSON ---
    before = _char_count()
    code, data = http_post("/api/v1/characters/import", "not{json")
    check("V-Import-13  invalid JSON -> 400 invalid_character_json, no row",
          code == 400 and data.get("detail") == "invalid_character_json"
          and _char_count() == before,
          f"code={code}")

    # --- Invalid import: non-dict JSON (array) ---
    before = _char_count()
    code, data = http_post("/api/v1/characters/import", '[1,2,3]')
    check("V-Import-14  non-dict JSON -> 400 invalid_character_json, no row",
          code == 400 and data.get("detail") == "invalid_character_json"
          and _char_count() == before,
          f"code={code}")

    # --- Invalid import: missing name ---
    before = _char_count()
    code, data = http_post("/api/v1/characters/import",
                           json.dumps({"description": "no name"}))
    check("V-Import-15  missing name -> 400 character_name_required, no row",
          code == 400 and data.get("detail") == "character_name_required"
          and _char_count() == before,
          f"code={code}")

    # --- Invalid import: empty name ---
    before = _char_count()
    code, data = http_post("/api/v1/characters/import",
                           json.dumps({"name": ""}))
    check("V-Import-16  empty name -> 400 character_name_required, no row",
          code == 400 and data.get("detail") == "character_name_required"
          and _char_count() == before,
          f"code={code}")

    # --- Invalid import: whitespace name ---
    before = _char_count()
    code, data = http_post("/api/v1/characters/import",
                           json.dumps({"name": "   "}))
    check("V-Import-17  whitespace name -> 400 character_name_required, no row",
          code == 400 and data.get("detail") == "character_name_required"
          and _char_count() == before,
          f"code={code}")

    # --- Import tags: non-list -> response tags=[] ---
    code, data = http_post("/api/v1/characters/import",
                           json.dumps({"name": "Tags Int", "tags": 123}))
    tags_int_id = data.get("id") if data else None
    if tags_int_id:
        _test_char_ids.append(tags_int_id)
    _tags_i = data.get("tags") if data else "?"
    check("V-Import-18  tags=123 (non-list) -> 201, response tags=[]",
          code == 201 and data is not None and data.get("tags") == [],
          f"code={code}, tags={_tags_i}")

    # --- Import tags: mixed list -> only strings kept ---
    code, data = http_post("/api/v1/characters/import",
                           json.dumps({"name": "Tags Mixed", "tags": ["ok", 1, "also"]}))
    tags_mixed_id = data.get("id") if data else None
    if tags_mixed_id:
        _test_char_ids.append(tags_mixed_id)
    _tags_m = data.get("tags") if data else "?"
    check("V-Import-19  tags=['ok',1,'also'] -> tags=['ok','also']",
          code == 201 and data is not None and data.get("tags") == ["ok", "also"],
          f"code={code}, tags={_tags_m}")

    # ══════════════════════════════════════════════════════════════════════════
    # V-Src  Static source analysis
    # ══════════════════════════════════════════════════════════════════════════
    section("V-Src  Static source analysis")

    chars_src_path = os.path.join(BACKEND_DIR, "routers", "characters.py")
    with open(chars_src_path, "r", encoding="utf-8") as f:
        chars_src = f.read()

    # Extract code lines only (skip comments and docstrings)
    _code_lines = []
    _in_docstring = False
    for line in chars_src.splitlines():
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
        check(f"{vid}  characters.py code has no '{keyword}'",
              keyword not in _code_text)

    # main.py checks — strip comments/docstrings to avoid false positives
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

    check("V-Src-8  main.py has active characters router import",
          "from routers import characters" in main_code)

    check("V-Src-9  main.py has active settings router import",
          "from routers import settings" in main_code)

    # Phase 5A+: models_router is active, completions must not be
    check("V-Src-10  main.py has active models_router import (Phase 5A+)",
          "from routers import models_router" in main_code)

    check("V-Src-11  main.py has active completions router (Phase 5B+)",
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

    # ── DB cleanup ────────────────────────────────────────────────────────────
    time.sleep(0.3)
    if _db_existed:
        # Delete ALL rows not in the pre-test snapshot (more robust than
        # relying on _test_char_ids, which may miss rows if a response id
        # was not captured before an error).
        try:
            with sqlite3.connect(_db_path) as _con:
                all_ids_now = {
                    r[0] for r in _con.execute(
                        "SELECT id FROM characters"
                    ).fetchall()
                }
                ids_to_delete = all_ids_now - _existing_char_ids
                if ids_to_delete:
                    _con.executemany(
                        "DELETE FROM characters WHERE id = ?",
                        [(i,) for i in ids_to_delete],
                    )
                    _con.commit()
                _removed = len(ids_to_delete)
        except Exception:
            _removed = -1
        print(f"  [info] {_removed} test rows removed from existing app.db.")
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
