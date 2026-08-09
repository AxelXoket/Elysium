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

import glob
import json
import msvcrt
import os
import shutil
import tempfile
import time
import urllib.error
import urllib.request

import secure_delete

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
_PREFIX = "elysium_verify_"
_LOCK_NAME = ".lock"
#: How long a finished run's vault stays around for a human to open. The fixed
#: TEST_PASSPHRASE above exists for exactly that, so this cannot be zero.
_MAX_AGE_S = 24 * 60 * 60

_DATA_DIR: str | None = tempfile.mkdtemp(prefix=_PREFIX)
#: Compared against realpath'd candidates when deciding what to reap, so it has
#: to be realpath'd itself. TEMP can resolve through a junction or an 8.3 short
#: name, and an asymmetric comparison would then fail to recognise this
#: process's OWN directory and reap it.
_DATA_DIR_REAL = os.path.realpath(_DATA_DIR)
os.environ["ELYSIUM_DATA_DIR"] = _DATA_DIR


def _take_lock(handle) -> bool:
    """Lock byte 0. Returns True when the lock was granted.

    seek(0) is not decoration. msvcrt.locking() locks `nbytes` starting at the
    CURRENT FILE POSITION, so a handle that has just written a byte is sitting
    at offset 1 and locks [1, 2) - while a fresh reader sits at 0 and probes
    [0, 1). Those ranges never overlap, so the probe always succeeded and every
    live directory looked dead. Both sides seek to 0 now, deliberately, and
    that is the whole contract.
    """
    try:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return True
    except OSError:
        return False


#: Held open, never closed, for the life of the process. Releasing it is what
#: tells another run's pruner that this directory is finished with - and
#: process exit releases it even when the exit is a crash, which is the whole
#: reason this is an OS lock and not a flag file.
#:
#: Wrapped, like the prune call at the bottom of this file: a harness that
#: cannot create its lock file (permissions, antivirus, a full disk) should
#: lose its liveness signal, not take all eleven verify scripts down at import.
_LOCK_HANDLE = None
try:
    _LOCK_HANDLE = open(os.path.join(_DATA_DIR, _LOCK_NAME), "w+b")
    _LOCK_HANDLE.write(b"\0")
    _LOCK_HANDLE.flush()
    _take_lock(_LOCK_HANDLE)
except OSError as _exc:                                    # pragma: no cover
    print(f"[harness] could not take the liveness lock: {_exc}")
    _LOCK_HANDLE = None


def data_dir() -> str:
    """The temp data dir this process uses for everything.

    One per PROCESS, not one per server start: a restart usually exists to
    prove state SURVIVED it, and hanging a new directory off each start
    would quietly turn those checks into tautologies.
    """
    if _DATA_DIR is None:
        raise RuntimeError("data_dir() after cleanup(): the vault is gone")
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


def _is_safe_to_delete(path: str) -> bool:
    """Is this path one of OUR temp dirs, and nothing else?

    Deliberately stdlib-only. This module does not import config and must not
    start: config.DATA_DIR is the REAL vault and it is one underscore away from
    this module's _DATA_DIR, so the two names have to stay in different files.
    The check below never has to know what the real vault is - it only ever
    approves a directory that is inside the system temp root AND carries our
    prefix.

    realpath first, because the name alone proves nothing: a junction planted
    at elysium_verify_<something> would otherwise send a delete anywhere at
    all. That is the same reparse-point trap secure_delete.is_redirected
    exists to close, and a pruner is a deletion call site like any other.
    """
    temp_root = os.path.realpath(tempfile.gettempdir())
    real = os.path.realpath(path)
    return (
        os.path.basename(real).startswith(_PREFIX)
        # A DIRECT child, not merely somewhere underneath. mkdtemp puts ours
        # exactly one level down, so anything deeper is somebody else's - and
        # pytest's own tmp_path trees live under the temp root too, which is
        # how a "startswith" version of this check approved a directory it had
        # no business touching.
        and os.path.dirname(real) == temp_root
    )


def _reap(path: str) -> None:
    """Shred what is inside, then remove the shell. Never bare rmtree.

    The directory holds app.db, salt.bin and verifier.bin: vault material,
    written by the real /vault/init route, holding whatever characters, chats
    and messages the run created. secure_delete.py's rule is that nothing
    unlinks that class of file directly, and a throwaway vault is still a
    vault. Idiom copied from browser_profile.sweep_dir, including the part
    that matters: if the shred pass found a redirected folder it REFUSED to
    enter, do not send rmtree in after it.
    """
    if not _is_safe_to_delete(path):
        print(f"[harness] refusing to remove {path!r}: not one of ours")
        return
    removed, stuck, pruned = secure_delete.shred_tree(path)
    if stuck:
        # Silence here is what let 136 of these pile up unnoticed for twelve
        # days. ignore_errors=True was the whole problem.
        print(f"[harness] {len(stuck)} file(s) resisted shredding in {path}: "
              f"{stuck[:3]}")
    if pruned:
        print(f"[harness] {path} contains a redirected folder; shell left in "
              f"place deliberately")
        return
    shutil.rmtree(path, ignore_errors=True)


def prune_old_dirs(max_age_s: float = _MAX_AGE_S) -> int:
    """Reap the temp vaults left behind by runs that are over.

    Called at import, which is the only moment every one of the eleven verify
    scripts reliably passes through.

    NOT delete-on-exit, and NOT keep-the-newest-N. Both were considered and
    both are wrong:

      - Deleting at exit would destroy the thing this module deliberately
        preserves. TEST_PASSPHRASE is fixed and hardcoded precisely so that a
        FAILED run leaves a vault a human can still open while diagnosing.
      - Keeping the newest N reaps by rank, not by liveness. A slow script
        that other runs outlive would have its directory deleted out from
        under its own uvicorn subprocess, mid-run.

    So liveness is decided by an exclusive lock, not by a guess: every live
    process holds .lock in its own directory for as long as it runs. If this
    process can take that lock, the owner is gone - exited or crashed - and
    the directory is safe. Age is only a courtesy window on top of that, so a
    human diagnosing yesterday's failure still finds it there.
    """
    reaped = 0
    now = time.time()
    for path in glob.glob(os.path.join(tempfile.gettempdir(), _PREFIX + "*")):
        if not os.path.isdir(path) or os.path.realpath(path) == _DATA_DIR_REAL:
            continue
        try:
            if now - os.path.getmtime(path) < max_age_s:
                continue
        except OSError:
            continue
        if _dir_is_live(path):
            continue
        _reap(path)
        reaped += 1
    return reaped


def _dir_is_live(path: str) -> bool:
    """True when another process still holds this directory's lock.

    A directory with no lock file at all is one of the 136 that predate this
    mechanism: nothing holds it, so it is reapable.
    """
    lock_path = os.path.join(path, _LOCK_NAME)
    if not os.path.exists(lock_path):
        return False
    try:
        handle = open(lock_path, "r+b")
    except OSError:
        return True          # cannot even open it: assume somebody has it
    try:
        if not _take_lock(handle):
            return True
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return False
    finally:
        handle.close()


def cleanup() -> None:
    """Remove THIS process's temp data dir. Safe to call twice.

    The bug this replaces was two bugs. It declared `global _data_dir` while
    the module variable is _DATA_DIR, so it raised NameError on the first line
    - and that never mattered, because nothing ever called it. All eleven
    verify scripts import this module and not one of them calls cleanup, which
    is the actual reason the directories accumulated.

    Left callable for a script that wants to tidy up after a clean run, but
    the accumulation is fixed by prune_old_dirs() at import instead, which
    does not depend on anybody remembering.
    """
    global _DATA_DIR, _LOCK_HANDLE
    if _LOCK_HANDLE is not None:
        _LOCK_HANDLE.close()
        _LOCK_HANDLE = None
    if _DATA_DIR is not None:
        _reap(_DATA_DIR)
        _DATA_DIR = None


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


# Runs LAST, after everything above is defined, and at import rather than from
# a call the scripts have to remember to make - the same reasoning that put
# ELYSIUM_DATA_DIR in os.environ at the top of this file. Eleven scripts, and
# the one thing every one of them reliably does is import this module.
#
# Deliberately not fatal: a pruner that could not clean up is a housekeeping
# problem, and taking the whole verify suite down over it would be a worse
# bug than the one being fixed.
try:
    _reaped = prune_old_dirs()
    if _reaped:
        print(f"[harness] reaped {_reaped} finished verify vault(s) from TEMP")
except Exception as exc:                                   # pragma: no cover
    print(f"[harness] prune skipped: {type(exc).__name__}: {exc}")
