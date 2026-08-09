"""verify/_harness.py left 136 test vaults in %TEMP% over twelve days.

Two defects, and the second is the one that mattered:

1. cleanup() declared `global _data_dir` while the module variable is
   _DATA_DIR, so its first line raised NameError.
2. Nothing ever called cleanup(). All eleven verify scripts import the module;
   not one of them calls it. That, not the NameError, is why the directories
   accumulated - fixing only the typo would have changed nothing at all.

The fix is a pruner that runs at import, because import is the one thing every
script reliably does. Deleting at exit was rejected: the module hardcodes a
fixed TEST_PASSPHRASE specifically so a FAILED run leaves a vault a human can
still open, and a delete-on-exit would destroy exactly that. Keeping the newest
N was rejected too - it reaps by rank rather than by liveness, so a slow script
that other runs outlive would have its vault deleted from under its own running
uvicorn.

So liveness is an exclusive OS lock, which process exit releases even when the
exit is a crash.

Every test below builds its own directories. NONE of them import _harness in
this process, and that is not squeamishness: importing it runs mkdtemp AND
sets os.environ["ELYSIUM_DATA_DIR"] for the rest of the pytest worker, and
calling cleanup() on the module-level singleton would delete a directory other
tests may still be pointed at - the very bug under test, relocated into the
suite. The module is driven in a subprocess instead, the same way the verify
scripts themselves are isolated.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

VERIFY_DIR = Path(__file__).resolve().parent.parent / "verify"
BACKEND_DIR = VERIFY_DIR.parent


def _run_harness(snippet: str, tmp_env: dict | None = None) -> str:
    """Import _harness in a FRESH process and run `snippet` against it."""
    code = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(BACKEND_DIR)!r})
        sys.path.insert(0, {str(VERIFY_DIR)!r})
        import _harness
        {textwrap.indent(textwrap.dedent(snippet), '        ').strip()}
    """)
    env = dict(os.environ)
    if tmp_env:
        env.update(tmp_env)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=120, env=env)
    assert out.returncode == 0, f"harness subprocess failed:\n{out.stderr}"
    return out.stdout


def _fake_vault(root: Path, name: str, *, locked: bool = False,
                age_s: float = 0.0) -> Path:
    """A directory shaped like a leftover verify run."""
    d = root / name
    d.mkdir()
    (d / "app.db").write_bytes(b"pretend-encrypted-database")
    (d / "salt.bin").write_bytes(b"\x01" * 16)
    (d / "verifier.bin").write_bytes(b"\x02" * 32)
    if locked:
        (d / ".lock").write_bytes(b"\0")
    if age_s:
        old = time.time() - age_s
        os.utime(d, (old, old))
    return d


# ── the pruner reaps what is finished ────────────────────────────────────────

def test_a_finished_vault_is_reaped(tmp_path):
    """The 136 that piled up have no lock file at all: nothing holds them."""
    stale = _fake_vault(tmp_path, "elysium_verify_stale", age_s=48 * 3600)
    out = _run_harness(
        f"""
        n = _harness.prune_old_dirs()
        print("reaped", n)
        """,
        tmp_env={"TEMP": str(tmp_path), "TMP": str(tmp_path)},
    )
    assert not stale.exists(), "a finished verify vault was left behind"
    assert "reaped" in out


def test_the_pruner_runs_without_being_asked(tmp_path):
    """The defect that actually caused the pile-up.

    cleanup() being broken never mattered, because no script called it. So a
    test that calls the pruner explicitly proves nothing about the bug: the
    fix is that IMPORTING the module is enough. This snippet imports and does
    nothing else.
    """
    stale = _fake_vault(tmp_path, "elysium_verify_untouched", age_s=48 * 3600)
    _run_harness(
        'print("imported and asked for nothing")',
        tmp_env={"TEMP": str(tmp_path), "TMP": str(tmp_path)},
    )
    assert not stale.exists(), (
        "importing the harness did not prune; the leak is still open"
    )


def test_a_young_vault_is_left_for_diagnosis(tmp_path):
    """A run that just failed must still be openable by a human."""
    fresh = _fake_vault(tmp_path, "elysium_verify_fresh", age_s=60)
    _run_harness(
        "_harness.prune_old_dirs()",
        tmp_env={"TEMP": str(tmp_path), "TMP": str(tmp_path)},
    )
    assert fresh.exists(), "yesterday's failure was destroyed, not preserved"


def test_a_live_vault_is_never_touched(tmp_path):
    """The failure mode that killed the keep-newest-N idea.

    The holder is a REAL second harness process, not a hand-rolled lock. An
    earlier version of this test opened the lock file itself and locked byte 0,
    which is not what the harness does - the harness writes a byte first, so
    its handle sits at offset 1 and locks [1, 2). The probe read [0, 1). The
    ranges never overlapped, the liveness check always answered "dead", and
    this test passed anyway because its fake lock happened to sit exactly where
    the probe looked. Green by coincidence.

    So: spawn a harness that holds its own directory, aged past the grace
    window, and let a second harness try to reap it.
    """
    holder = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(f"""
            import sys, os, time
            sys.path.insert(0, {str(BACKEND_DIR)!r})
            sys.path.insert(0, {str(VERIFY_DIR)!r})
            import _harness
            d = _harness.data_dir()
            old = time.time() - 48 * 3600
            os.utime(d, (old, old))
            print(d, flush=True)
            time.sleep(60)
        """)],
        stdout=subprocess.PIPE, text=True,
        env={**os.environ, "TEMP": str(tmp_path), "TMP": str(tmp_path)},
    )
    try:
        live = Path(holder.stdout.readline().strip())
        assert live.is_dir(), "the holder never reported its directory"
        assert (live / ".lock").exists(), "the harness did not take a lock"

        _run_harness(
            "_harness.prune_old_dirs()",
            tmp_env={"TEMP": str(tmp_path), "TMP": str(tmp_path)},
        )
        assert live.exists(), "the pruner deleted a running script's vault"
        assert (live / "app.db").exists() or True   # holder writes no app.db
    finally:
        holder.kill()
        holder.wait(timeout=30)


def test_the_lock_is_released_when_the_holder_dies(tmp_path):
    """A crash must not protect a directory forever.

    This is the other half of the contract, and the reason liveness is an OS
    lock rather than a flag file: the operating system releases it on process
    exit whether that exit was clean or not.
    """
    holder = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(f"""
            import sys, os, time
            sys.path.insert(0, {str(BACKEND_DIR)!r})
            sys.path.insert(0, {str(VERIFY_DIR)!r})
            import _harness
            d = _harness.data_dir()
            old = time.time() - 48 * 3600
            os.utime(d, (old, old))
            print(d, flush=True)
            time.sleep(60)
        """)],
        stdout=subprocess.PIPE, text=True,
        env={**os.environ, "TEMP": str(tmp_path), "TMP": str(tmp_path)},
    )
    live = Path(holder.stdout.readline().strip())
    assert live.is_dir()
    holder.kill()
    holder.wait(timeout=30)

    _run_harness(
        "_harness.prune_old_dirs()",
        tmp_env={"TEMP": str(tmp_path), "TMP": str(tmp_path)},
    )
    assert not live.exists(), (
        "a crashed run's vault was kept forever: the lock outlived its owner"
    )


def test_the_pruner_never_leaves_the_temp_root(tmp_path):
    """The guard, not the glob, is what makes deletion safe.

    A directory carrying our prefix but living somewhere else must be refused
    even when handed to the reaper directly.
    """
    outsider = tmp_path / "elsewhere" / "elysium_verify_impostor"
    outsider.mkdir(parents=True)
    (outsider / "app.db").write_bytes(b"not ours to delete")

    out = _run_harness(
        f"""
        print("safe:", _harness._is_safe_to_delete({str(outsider)!r}))
        _harness._reap({str(outsider)!r})
        """,
        tmp_env={"TEMP": str(tmp_path / "realtemp"), "TMP": str(tmp_path / "realtemp")},
    )
    assert "safe: False" in out
    assert outsider.exists(), "the reaper deleted a path outside the temp root"
    assert (outsider / "app.db").read_bytes() == b"not ours to delete"


def test_a_directory_without_our_prefix_is_refused(tmp_path):
    other = tmp_path / "somebody_elses_data"
    other.mkdir()
    (other / "important.txt").write_bytes(b"keep me")
    out = _run_harness(
        f"""
        print("safe:", _harness._is_safe_to_delete({str(other)!r}))
        _harness._reap({str(other)!r})
        """,
        tmp_env={"TEMP": str(tmp_path), "TMP": str(tmp_path)},
    )
    assert "safe: False" in out
    assert (other / "important.txt").exists()


# ── cleanup() works now, and works twice ─────────────────────────────────────

def test_cleanup_removes_this_process_own_vault(tmp_path):
    """It used to raise NameError on its first line."""
    out = _run_harness(
        """
        d = _harness.data_dir()
        import os
        print("existed:", os.path.isdir(d))
        _harness.cleanup()
        print("gone:", not os.path.exists(d))
        """,
        tmp_env={"TEMP": str(tmp_path), "TMP": str(tmp_path)},
    )
    assert "existed: True" in out
    assert "gone: True" in out


def test_cleanup_is_safe_to_call_twice(tmp_path):
    out = _run_harness(
        """
        _harness.cleanup()
        _harness.cleanup()
        print("survived")
        """,
        tmp_env={"TEMP": str(tmp_path), "TMP": str(tmp_path)},
    )
    assert "survived" in out


def test_importing_the_harness_still_isolates_the_data_dir(tmp_path):
    """The property the whole module exists for, unchanged by this work."""
    out = _run_harness(
        """
        import os
        print("env:", os.environ["ELYSIUM_DATA_DIR"] == _harness.data_dir())
        print("prefix:", os.path.basename(_harness.data_dir()).startswith(
            "elysium_verify_"))
        """,
        tmp_env={"TEMP": str(tmp_path), "TMP": str(tmp_path)},
    )
    assert "env: True" in out
    assert "prefix: True" in out
