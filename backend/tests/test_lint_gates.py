"""test_lint_gates.py - the lint config is only a gate if something runs it.

KADEME 21. `backend/ruff.toml` narrows ruff to the engine worker directory.
A config file on its own is the failure this project already measured once:
sixteen scripts under backend/verify/, nobody running them, three dead. So
the config gets a caller, and the caller lives in the suite everybody runs.

What ruff buys here that a parse test does not: it walks the DIRECTORY. A
worker added tomorrow is covered without anybody editing a list.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
WORKER = BACKEND / "tts" / "worker"

#: Kept in step with backend/ruff.toml. Spelled out rather than read from the
#: file, so widening the config quietly does not also widen this test's idea
#: of what it is checking.
SELECT = ["F"]


def _ruff(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--no-cache", *args],
        cwd=BACKEND, capture_output=True, text=True, timeout=180,
    )


def _have_ruff() -> bool:
    try:
        return subprocess.run([sys.executable, "-m", "ruff", "--version"],
                              capture_output=True, timeout=60).returncode == 0
    except Exception:
        return False


needs_ruff = pytest.mark.skipif(
    not _have_ruff(),
    reason="ruff is not installed in this interpreter (pip install ruff)",
)


@needs_ruff
def test_the_worker_directory_is_clean():
    """Syntax errors and undefined names in a worker do not surface until an
    engine venv we do not control tries to run the file - on a user's
    machine, as 'the environment is broken'."""
    done = _ruff(str(WORKER))
    assert done.returncode == 0, done.stdout or done.stderr


@needs_ruff
def test_the_config_actually_narrows_to_the_worker():
    """The `include` line is the whole safety of this gate. Measured in
    KADEME 21: `ruff check backend --select E9,F` reports 119 findings, so a
    config that quietly widened would either turn the suite red for a
    multi-day backlog nobody agreed to, or - if somebody then relaxed the
    selection to make it pass - leave the worker unguarded."""
    done = _ruff(".")
    assert done.returncode == 0, done.stdout or done.stderr
    listed = _ruff(".", "--show-files")
    files = [line for line in listed.stdout.splitlines() if line.strip()]
    assert files, "the include pattern matched NO files - the gate is empty"
    assert all("worker" in f.replace("\\", "/") for f in files), (
        "ruff is checking outside the worker directory:\n%s"
        % "\n".join(f for f in files if "worker" not in f.replace("\\", "/"))[:600]
    )


@needs_ruff
def test_a_syntax_error_is_caught(tmp_path):
    """POSITIVE CONTROL. Both tests above pass by finding nothing, which is
    also what a broken invocation, an empty file list or a selection that
    matched no rules would do."""
    bad = tmp_path / "broken.py"
    bad.write_text("def f(:\n    pass\n", encoding="utf-8")
    done = _ruff("--select", ",".join(SELECT), "--isolated", str(bad))
    assert done.returncode != 0, "a file that cannot be parsed was reported clean"
    # Reported as `invalid-syntax`, not under a rule code. Measured in KADEME
    # 21: on ruff 0.16.3 a parse failure fires whatever the selection is - so
    # this control proves syntax coverage survives ANY future narrowing of
    # `select`, which is exactly why E9 was dropped from the config.
    assert "invalid-syntax" in done.stdout, done.stdout


@needs_ruff
def test_an_undefined_name_is_caught(tmp_path):
    """The other half. A worker that references a name it never imported runs
    fine until the branch is taken, which for an engine worker is late."""
    bad = tmp_path / "undefined.py"
    bad.write_text(textwrap.dedent("""
        def synth(text):
            return numpy.zeros(3)
    """), encoding="utf-8")
    done = _ruff("--select", ",".join(SELECT), "--isolated", str(bad))
    assert done.returncode != 0, "an undefined name was reported clean"
    assert "F821" in done.stdout, done.stdout


@needs_ruff
def test_clean_code_is_not_flagged(tmp_path):
    """And the control is discriminating, not just always-red."""
    ok = tmp_path / "fine.py"
    ok.write_text(textwrap.dedent("""
        import math


        def synth(text):
            return math.floor(len(text))
    """), encoding="utf-8")
    done = _ruff("--select", ",".join(SELECT), "--isolated", str(ok))
    assert done.returncode == 0, done.stdout
