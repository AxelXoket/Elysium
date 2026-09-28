"""The one way a test in this suite runs git.

Every git call in backend/tests goes through run_git() or passes git_env() as
its environment. Two things are wrong with the environment a test inherits,
and both have bitten or would bite:

  1. Inside a git hook it names the repository the hook is running for
     (GIT_DIR, GIT_INDEX_FILE, GIT_COMMON_DIR and the rest). A test that ran
     `git init` in tmp_path with that environment re-initialised the real
     repository instead, wrote a test identity and core.hooksPath into its
     config, and committed onto the branch being pushed. The list of what to
     strip lives in backend/verify/git_env.py, because run_gates.py strips the
     same list from every process it starts.

  2. It reads this machine's git configuration: the system file, the global
     file, and whatever ~/.config/git/ignore and attributes say. A test that
     passes because of a setting on this machine is measuring the machine, and
     `git config --global` from a test would write into the real global file. So
     HOME points at an empty folder made for this run, the global config file
     lives there, and the system file is switched off.
"""

from __future__ import annotations

import atexit
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType

import pytest

_SCRUB_PATH = Path(__file__).resolve().parent.parent / "verify" / "git_env.py"


def _load_scrub() -> ModuleType:
    # Loaded by path, like verify_hygiene in test_hygiene_gate.py, because
    # backend/verify is not a package. Reused when test_gate_lanes.py has
    # already imported it through sys.path, so the suite holds one copy.
    existing = sys.modules.get("git_env")
    if existing is not None and Path(existing.__file__).resolve() == _SCRUB_PATH:
        return existing
    spec = importlib.util.spec_from_file_location("git_env", _SCRUB_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["git_env"] = module
    spec.loader.exec_module(module)
    return module


#: backend/verify/git_env.py. Called through this attribute, never copied out
#: of it, so a test can swap the scrub for a no-op and watch what it prevents.
scrub = _load_scrub()

#: An empty folder that stands in for the owner's home for git's sake. Made
#: once, when conftest imports this module, and removed at exit.
#:
#: At import and not on first use, on purpose: by the time a test runs,
#: fs_guard has wrapped mkdir and rmtree, and the removal at exit is not a
#: test calling it, so the guard refused it as application code writing
#: straight into %TEMP%. Measured on the first run of this file. Both the
#: folder and the function that removes it are taken before any guard exists.
_HOME = tempfile.mkdtemp(prefix="elysium-git-home-")
atexit.register(shutil.rmtree, _HOME, ignore_errors=True)


def empty_home() -> str:
    """The folder git_env() hands git as HOME. Nothing in it is read back."""
    return _HOME


def git_env() -> dict[str, str]:
    """The environment every git command in the suite runs with."""
    env = scrub.without_redirects(os.environ)
    home = empty_home()
    env["HOME"] = home
    env["XDG_CONFIG_HOME"] = os.path.join(home, ".config")
    env["GIT_CONFIG_GLOBAL"] = os.path.join(home, ".gitconfig")
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def run_git(*args: str, cwd: Path | str,
            **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run(["git", *args]) under git_env(). Never inherits."""
    if "env" in kwargs:
        raise TypeError("run_git builds the environment itself; pass none")
    return subprocess.run(["git", *args], cwd=str(cwd), env=git_env(),
                          **kwargs)


def isolate_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make os.environ equal git_env() for the length of one test.

    For code under test that starts git with whatever environment it
    inherits, verify_hygiene.py above all. Pointing its REPO_ROOT at a
    scratch repository does nothing while GIT_DIR outranks the working
    directory.
    """
    env = git_env()
    for name in list(os.environ):
        if name not in env:
            monkeypatch.delenv(name)
    for name, value in env.items():
        monkeypatch.setenv(name, value)


def strip_process_redirects() -> list[str]:
    """Remove git's redirects from os.environ for the whole run.

    Called once from conftest.py at import. A third wall, for a test that
    calls git without run_git() when pytest was started from a hook some way
    other than run_gates.py. Returns what it removed, for the record.
    """
    removed = [name for name in list(os.environ) if scrub.is_redirecting(name)]
    for name in removed:
        del os.environ[name]
    return removed


#: The identity a scratch repository commits under. A reserved domain, so it
#: can never be anybody's address, and exactly what turned up in the real
#: repository's config when this path inherited a hook's GIT_DIR.
SCRATCH_EMAIL = "t@example.invalid"
SCRATCH_NAME = "t"


def init_scratch_repo(path: Path) -> Path:
    """A fresh repository at `path` with a local identity, and nothing else.

    The identity goes into the scratch repository's own config. That write is
    the one that reached the real repository when this lived inline in
    test_hygiene_gate.py and inherited a hook's GIT_DIR.
    """
    path.mkdir(parents=True, exist_ok=True)
    run_git("init", "-q", cwd=path, check=True)
    run_git("config", "user.email", SCRATCH_EMAIL, cwd=path, check=True)
    run_git("config", "user.name", SCRATCH_NAME, cwd=path, check=True)
    return path
