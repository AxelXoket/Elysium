"""
Phase 1 verification script.
Run from backend/ with the virtual environment active:
    .venv/Scripts/python verify_phase1.py

Sections:
  V1-V5   Database schema (isolated OS-temp DB, no project artifacts)
  V6-V7   Server binds to 127.0.0.1:8787 and GET /healthz returns {"ok":true}
  V8      Keyring backend is secure and functional
  V9-V10  HTTP client uses trust_env=False; get_client() never raises
  V11-V12 Proxy health short-circuit paths (no network required)
  V13-V16 Generation parameter whitelist, range validation, OpenRouterError
"""

import sys
import os
import json
import sqlite3
import asyncio
import inspect
import subprocess
import tempfile
import shutil
import time
import urllib.request

# Force UTF-8 so Unicode labels do not crash on cp1254 terminals.
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

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Temp DB setup ─────────────────────────────────────────────────────────────
# All in-process test DB artifacts go to the OS temp dir.
# Nothing ever lands inside the project tree.
_tmp_dir = tempfile.mkdtemp(prefix="chatbot_phase1_")
TEST_DB = os.path.join(_tmp_dir, "verify.db")

import database
database.DB_PATH = TEST_DB   # patch before any init_db() call

# ── V1-V5  Database schema ────────────────────────────────────────────────────
section("V1-V5  Database schema")

database.init_db()
check("V1  DB file created", os.path.exists(TEST_DB))

with sqlite3.connect(TEST_DB) as con:
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    check("V2  all 4 tables present",
          {"settings", "characters", "chats", "messages"} <= tables,
          str(tables))

    char_info = {r[1]: r for r in con.execute("PRAGMA table_info(characters)").fetchall()}
    check("V3a characters.raw_json exists",             "raw_json"                 in char_info)
    check("V3b characters.post_history_instruction",    "post_history_instruction" in char_info)

    chat_cols = {r[1]: r for r in con.execute("PRAGMA table_info(chats)").fetchall()}
    notnull = chat_cols["model_id"][3] if "model_id" in chat_cols else -1
    check("V4  chats.model_id nullable (notnull=0)", notnull == 0, f"notnull={notnull}")

    wal = con.execute("PRAGMA journal_mode;").fetchone()[0]
    check("V5  WAL mode active", wal == "wal", f"journal_mode={wal}")

# ── V6-V7  Server bind + /healthz ────────────────────────────────────────────
section("V6-V7  Server bind (127.0.0.1:8787) + GET /healthz")

_uvicorn_name = "uvicorn.exe" if sys.platform == "win32" else "uvicorn"
UVICORN_EXE = os.path.join(os.path.dirname(sys.executable), _uvicorn_name)
_server_db = os.path.join(BACKEND_DIR, "app.db")
_server_db_existed = os.path.exists(_server_db)

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

    # Poll until the server accepts connections (max 6 s).
    started = False
    for _ in range(30):
        time.sleep(0.2)
        try:
            urllib.request.urlopen("http://127.0.0.1:8787/healthz", timeout=1)
            started = True
            break
        except Exception:
            continue

    check("V6  server accepts connections on 127.0.0.1:8787", started)

    if started:
        with urllib.request.urlopen("http://127.0.0.1:8787/healthz", timeout=5) as resp:
            body = json.loads(resp.read().decode())
        check('V7  GET /healthz returns {"ok": true}',
              body == {"ok": True}, f"got={body}")
    else:
        check('V7  GET /healthz returns {"ok": true}', False, "server did not start in time")

except FileNotFoundError:
    check("V6  server accepts connections on 127.0.0.1:8787", False,
          f"uvicorn not found at {UVICORN_EXE}")
    check('V7  GET /healthz returns {"ok": true}', False, "uvicorn not found")
except Exception as exc:
    check("V6  server accepts connections on 127.0.0.1:8787", False, str(exc))
    check('V7  GET /healthz returns {"ok": true}', False, "server error")
finally:
    if server_proc is not None:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_proc.kill()
            server_proc.wait()
    # Only remove app.db files if they were created by this test run.
    time.sleep(0.2)   # brief wait to ensure process has released file handles
    if _server_db_existed:
        print("  [info] app.db existed before test — preserved.")
    else:
        for ext in ("", "-wal", "-shm"):
            p = _server_db + ext
            if os.path.exists(p):
                try:
                    os.remove(p)
                except PermissionError:
                    pass
        print("  [info] app.db created by test — removed.")

# ── V8  Keyring backend ────────────────────────────────────────────────────────
section("V8  Keyring backend")

from config import KEYRING_PROXY_URL
from keyring_service import verify_keyring_backend, set_secret, get_secret, delete_secret

# Backup proxy URL before any test deletes it.
_bak_proxy_url = get_secret(KEYRING_PROXY_URL)

try:
    try:
        verify_keyring_backend()
        check("V8a verify_keyring_backend() passes", True)
    except RuntimeError as e:
        check("V8a verify_keyring_backend() passes", False, str(e)[:120])

    _TK = "_v1_test_key"
    set_secret(_TK, "hello")
    val = get_secret(_TK)
    delete_secret(_TK)
    check("V8b round-trip correct", val == "hello", f"got={val!r}")

    try:
        delete_secret(_TK)   # second delete must be silent
        check("V8c delete missing key is silent", True)
    except Exception as e:
        check("V8c delete missing key is silent", False, str(e))

    # ── V9-V10  Network client ────────────────────────────────────────────────
    section("V9-V10  Network client")

    import httpx
    import network_client as nc

    src = inspect.getsource(nc._build_client)
    occurrences = src.count("trust_env=False")
    check("V9  trust_env=False in both client branches", occurrences >= 2, f"count={occurrences}")

    nc._client = None
    delete_secret(KEYRING_PROXY_URL)

    try:
        client = nc.get_client()
        check("V10 get_client() with empty keyring does not raise",
              isinstance(client, httpx.AsyncClient))
    except Exception as e:
        check("V10 get_client() with empty keyring does not raise", False, str(e))

    # ── V11-V12  Proxy health ─────────────────────────────────────────────────
    section("V11-V12  Proxy health (short-circuit paths, no network)")

    import proxy_health as ph
    from database import get_db

    async def _health_tests() -> None:
        # Scenario 1: proxy_required=false, no URL -> healthy, no probe
        with get_db() as con:
            con.execute("DELETE FROM settings")
            con.execute("INSERT INTO settings VALUES ('proxy_required','0')")
        delete_secret(KEYRING_PROXY_URL)
        ph.invalidate_health_cache()
        r = await ph.check_proxy_health()
        check("V11 proxy_required=false + no URL -> healthy=True", r["healthy"] is True, str(r))
        check("V11 proxy_required=false + no URL -> reason=None",  r["reason"] is None,  str(r))

        # Scenario 2: proxy_required=true, no URL -> proxy_missing, no probe
        with get_db() as con:
            con.execute("UPDATE settings SET value='1' WHERE key='proxy_required'")
        ph.invalidate_health_cache()
        r = await ph.check_proxy_health()
        check("V12 proxy_required=true + no URL -> healthy=False",        r["healthy"] is False,           str(r))
        check("V12 proxy_required=true + no URL -> reason=proxy_missing", r["reason"] == "proxy_missing",  str(r))

    asyncio.run(_health_tests())

    # ── V13-V16  Generation param validation ──────────────────────────────────
    section("V13-V16  Generation param validation")

    from openrouter import validate_and_filter_gen_params, OpenRouterError

    r = validate_and_filter_gen_params({"unknown_key": 99, "another": 1})
    check("V13 unknown keys silently dropped", r == {}, f"got={r}")

    try:
        validate_and_filter_gen_params({"temperature": 5.0})
        check("V14 temperature=5.0 raises ValueError", False, "no exception raised")
    except ValueError as e:
        check("V14 temperature=5.0 raises ValueError", True, str(e))

    r = validate_and_filter_gen_params({"temperature": 0.9, "max_tokens": 512})
    check("V15a valid params pass through",
          r == {"temperature": 0.9, "max_tokens": 512}, f"got={r}")

    r = validate_and_filter_gen_params({"temperature": None, "top_p": 0.9})
    check("V15b None values dropped", r == {"top_p": 0.9}, f"got={r}")

    r = validate_and_filter_gen_params({"temperature": 0.0})
    check("V15c temperature=0.0 lower bound accepted", r == {"temperature": 0.0}, f"got={r}")
    r = validate_and_filter_gen_params({"temperature": 2.0})
    check("V15c temperature=2.0 upper bound accepted", r == {"temperature": 2.0}, f"got={r}")

    r = validate_and_filter_gen_params({"max_tokens": 100})
    check("V15d max_tokens returned as int",
          isinstance(r.get("max_tokens"), int), f"type={type(r.get('max_tokens'))}")

    err = OpenRouterError("openrouter_timeout")
    check("V16 OpenRouterError carries reason attribute",
          err.reason == "openrouter_timeout", f"got={err.reason!r}")

finally:
    # ── Restore keyring proxy URL ─────────────────────────────────────────────
    if _bak_proxy_url is not None:
        set_secret(KEYRING_PROXY_URL, _bak_proxy_url)
    else:
        delete_secret(KEYRING_PROXY_URL)

    # ── Cleanup ───────────────────────────────────────────────────────────────
    # All in-process test DB files are in the OS temp dir — remove the whole tree.
    shutil.rmtree(_tmp_dir, ignore_errors=True)

# ── Summary ───────────────────────────────────────────────────────────────────
section("Summary")

passed = sum(1 for _, ok in results if ok)
total  = len(results)
print(f"  {passed}/{total} checks passed\n")
if passed < total:
    print("  FAILED:")
    for label, ok in results:
        if not ok:
            print(f"    x {label}")
    print()

sys.exit(0 if passed == total else 1)

