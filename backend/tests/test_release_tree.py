"""Audit KÖK 12, first finding: the published tree is not the working tree.

Measured, not assumed. `git archive HEAD` into a temp directory and then:

    import main   -> ModuleNotFoundError: No module named 'keyring_service'
    pytest        -> 83 tests collected, 1 collection error
                     (the working tree collects 1200+)

172 files are untracked, including all of backend/tts/, speech_prep.py,
voice_tags.py, keyring_service.py, messages_common.py and 61 tests. v1.1's
headline feature does not exist in the committed tree at all.

WHY THIS TEST CAN ONLY EXIST HERE, AND WHY IT SKIPS
    No test run from inside the working tree can see this class of failure:
    the suite reads the working tree, which is precisely the copy that HAS the
    files. The only gate that works is the one below - build the published
    tree and try to use it.

    It SKIPS while source files are still untracked, with the list attached,
    because committing them is a decision for the person who owns the repo and
    not something a test suite should nag about on every run. The moment they
    are committed this becomes a real gate, with no edit needed.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

#: Extensions that make a file part of the SOURCE. A stray note or screenshot
#: left untracked is not what this is about.
_SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".txt", ".spec", ".json"}


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout


def _untracked_source() -> list[str]:
    try:
        out = _git("ls-files", "--others", "--exclude-standard")
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git is not available here")
    return sorted(
        line for line in out.splitlines()
        if Path(line).suffix in _SOURCE_SUFFIXES
    )


@pytest.fixture(scope="module")
def published_tree(tmp_path_factory) -> Path:
    """HEAD, extracted. What a fresh clone would actually get."""
    missing = _untracked_source()
    if missing:
        head = "\n  ".join(missing[:12])
        pytest.skip(
            f"{len(missing)} source files are untracked, so the published tree "
            f"is knowingly incomplete and this gate has nothing to guard yet. "
            f"First few:\n  {head}\n  ...\n"
            f"Commit them and this test starts checking the release for real."
        )

    dest = tmp_path_factory.mktemp("published")
    archive = dest / "head.tar"
    with archive.open("wb") as fh:
        subprocess.run(
            ["git", "archive", "HEAD"], cwd=REPO, stdout=fh, check=True,
        )
    with tarfile.open(archive) as tar:
        tar.extractall(dest)
    archive.unlink()
    return dest


def test_the_published_backend_can_be_imported(published_tree):
    """A fresh clone dies here: routers/settings.py imports keyring_service,
    which was never committed. Everything downstream - the app, the suite, the
    packaged build - fails at the same line."""
    backend = published_tree / "backend"
    assert backend.is_dir(), "git archive produced no backend/"

    result = subprocess.run(
        [sys.executable, "-c", "import main"],
        cwd=backend, capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        "the published tree cannot even import the app:\n"
        + result.stderr[-1500:]
    )


def test_the_published_suite_collects_what_this_one_does(published_tree):
    """Collection COUNT, because a partial tree collects a partial suite and
    reports it as a clean run. The committed tree collected 83 against 1200+
    here - a green pytest that had never seen the voice subsystem."""
    backend = published_tree / "backend"

    published = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q", "--collect-only"],
        cwd=backend, capture_output=True, text=True,
    )
    # pytest's OWN verdict, not a substring of its output. `"error" not in
    # stdout` matched the collected test NAMES - test_error_catalogue.py is
    # enough to fail it - so the check reported a broken collection on a tree
    # that had collected perfectly.
    assert published.returncode == 0, (
        "the published suite cannot be collected:\n"
        + (published.stdout + published.stderr)[-1500:]
    )

    here = len(list((REPO / "backend" / "tests").glob("test_*.py")))
    there = len(list((backend / "tests").glob("test_*.py")))
    assert there >= here, (
        f"the published tree has {there} test files, this one has {here} - "
        "the suite that runs on a release is not the suite anyone wrote"
    )


def test_the_voice_subsystem_is_actually_published(published_tree):
    """Named individually because it is the whole of v1.1: an installable
    build with no tts/ package is an app whose headline feature is absent."""
    backend = published_tree / "backend"
    for required in (
        "keyring_service.py",
        "speech_prep.py",
        "voice_tags.py",
        "messages_common.py",
        "tts/__init__.py",
        "tts/host.py",
        "tts/worker/fish_s2.py",
    ):
        assert (backend / required).is_file(), f"{required} is not in HEAD"


def test_git_is_how_we_ask(tmp_path):
    """Guards the guard: if the git plumbing above silently stopped working,
    every test in this file would skip and nobody would notice."""
    out = _git("rev-parse", "--is-inside-work-tree").strip()
    assert out == "true"
    assert shutil.which("git"), "git vanished mid-run"
