"""Shared process harness for the verify scripts.

These scripts start a real uvicorn in a subprocess and drive it over HTTP.
They got two things wrong in the same way, and the second hid the first.

1. ISOLATION. `config._resolve_data_dir()` falls back to the directory
   holding config.py when the build is not frozen - that is `backend/` - so
   a subprocess started without ELYSIUM_DATA_DIR opens `backend/app.db`:
   the developer's REAL vault, holding their chats, personas and API key.
   These scripts create characters, chats and messages and delete them
   again on the way out. Every run mutated live data, and a run that died
   between the create and the cleanup left its litter behind.

2. UNLOCK. The vault shipped after these scripts were written. The server
   starts locked by design, so every data route answers 423 and the suite
   dies on its first assertion. pytest sidesteps this by calling
   `vault_state.set_key()` in-process; a subprocess cannot reach into the
   server's memory, so it goes through the real /vault/init route - which
   is better coverage than the shortcut, not worse.

Nothing here touches the developer's data dir. `isolated_env()` is the only
supported way for a verify script to build the environment it hands to
uvicorn, and it always points ELYSIUM_DATA_DIR at a fresh temp directory.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import urllib.error
import urllib.request

# Long enough to clear MIN_PASSPHRASE_LEN, fixed so a failed run leaves a
# temp vault a human can still open while diagnosing.
TEST_PASSPHRASE = "verify-harness-passphrase"

# Set ON IMPORT, deliberately, rather than from a call the scripts have to
# remember to make. Four of the eleven start uvicorn with no `env=` argument
# at all, so the child inherits this process's environment and nothing else -
# a per-Popen fix would have isolated seven scripts and silently left four
# pointed at the real vault. Putting it in os.environ covers both shapes at
# once, and it must happen before anything imports config, whose DATA_DIR is
# resolved exactly once at module import.
_DATA_DIR: str = tempfile.mkdtemp(prefix="elysium_verify_")
os.environ["ELYSIUM_DATA_DIR"] = _DATA_DIR


def data_dir() -> str:
    """The temp data dir this process uses for everything.

    One per PROCESS, not one per server start: a restart usually exists to
    prove state SURVIVED it, and hanging a new directory off each start
    would quietly turn those checks into tautologies.
    """
    return _DATA_DIR


def isolated_env(**extra: str) -> dict[str, str]:
    """A child environment pointed at `data_dir()` instead of backend/.

    os.environ already carries ELYSIUM_DATA_DIR by the time this runs, so
    this is belt and braces - it stays because it says at the call site what
    the copy is for.
    """
    env = os.environ.copy()
    env["ELYSIUM_DATA_DIR"] = data_dir()
    env.update(extra)
    return env


def cleanup() -> None:
    """Remove the temp data dir. Safe to call twice, safe to never call."""
    global _data_dir
    if _data_dir is not None:
        shutil.rmtree(_data_dir, ignore_errors=True)
        _data_dir = None


def _request(url: str, payload: dict | None = None, method: str = "GET",
             timeout: float = 10.0) -> tuple[int, dict]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    # The CSRF gate wants a same-origin declaration on writes; the scripts
    # that predate it were only ever hitting reads.
    req.add_header("Origin", url.split("/api/")[0])
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode() or "{}"
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode() or "{}"
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"detail": body[:200]}


def wait_for_healthz(base: str, seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"{base}/healthz", timeout=1)
            return True
        except Exception:
            time.sleep(0.2)
    return False


def open_vault(api_base: str, passphrase: str = TEST_PASSPHRASE) -> None:
    """Make the data routes answer. Idempotent across server restarts.

    First run on a fresh temp dir initializes; every later run on the same
    dir unlocks. Raises RuntimeError rather than returning a flag, because a
    verify script that keeps going past this point reports dozens of 423s
    that all mean 'the harness did not work' and none of which mean what
    their label says.
    """
    status_code, status = _request(f"{api_base}/vault/status")
    if status_code != 200:
        raise RuntimeError(f"/vault/status answered {status_code}: {status}")
    if status.get("unlocked"):
        return

    route = "unlock" if status.get("initialized") else "init"
    code, body = _request(f"{api_base}/vault/{route}",
                          {"passphrase": passphrase}, method="POST")
    if code != 200:
        raise RuntimeError(f"/vault/{route} answered {code}: {body}")

    code, status = _request(f"{api_base}/vault/status")
    if not status.get("unlocked"):
        raise RuntimeError(f"vault still locked after /vault/{route}: {status}")
