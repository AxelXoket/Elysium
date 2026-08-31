"""No name a person reads on screen sits in tracked source. U-56.

WHY THIS FILE CANNOT CONTAIN THE THING IT LOOKS FOR

The defect was a real person's own voice label, written out as a string
literal in a measurement script and in two test files, and published from
there to a public GitHub repository under a release tag. A gate that spelled
that name out in order to search for it would be another copy of the same
defect, in a file that ships with the repo.

So the list lives OUTSIDE the tree. Two ways to arm it:

  * ELYSIUM_BANNED_NAMES - comma separated, for a one-off run;
  * backend/tests/.banned_names - one per line, `#` comments allowed, and
    listed in .gitignore so it can never be committed.

With neither present the scan SKIPS, and says so with the reason. That is a
deliberate weakness and it is written down rather than hidden: an unarmed
gate proves nothing about names, which is why the scanner's own correctness
is proved separately below, by tests that always run against synthetic
names. Half of this file is always live; the half that needs the real list is
the half that cannot be shipped.

WHAT COUNTS AS SOURCE

Whatever `git ls-files` reports, which is exactly the set that gets
published. `.venv`, `.git`, build output and every other untracked thing are
outside it by construction rather than by an exclusion list somebody has to
remember to update.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

#: Where a local, never-committed list may sit.
LOCAL_LIST = Path(__file__).resolve().parent / ".banned_names"

#: The environment variable that arms the scan for one run.
ENV_VAR = "ELYSIUM_BANNED_NAMES"

#: Files big enough that reading them is pointless: a name is text.
_MAX_BYTES = 4 * 1024 * 1024

#: Suffixes that are not text at all.
_BINARY = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".pdf", ".zip",
    ".exe", ".dll", ".pyd", ".so", ".woff", ".woff2", ".ttf", ".onnx",
    ".safetensors", ".bin", ".wav", ".mp3", ".ogg", ".mp4",
})

#: A string that exists nowhere else in this repository, on purpose.
#:
#: The positive control needs the scanner to find something REAL in the
#: tracked corpus, not in a tmp_path fake. It has to be a plain literal: an
#: assembled one ("can" + "ary") would not be in the file text, and the scan
#: reads text.
CANARY = "canary-for-the-name-gate-9f3c1"

#: What `git ls-files -z` puts between two names.
_NUL = bytes([0])


def tracked_files(root: Path) -> list[Path]:
    """Every file git tracks under `root`, as absolute paths.

    Untracked and ignored files are absent by construction, which is the
    point: the question this gate asks is what got PUBLISHED, and that is
    exactly the tracked set.
    """
    out = subprocess.run(["git", "ls-files", "-z"], cwd=str(root),
                         capture_output=True, check=True)
    return [root / name.decode("utf-8", "replace")
            for name in out.stdout.split(_NUL) if name]


def scan(files, needles) -> dict[str, list[str]]:
    """Which of `needles` appears in which file. Case-insensitive.

    Case folds because the same label reaches source both as a lowercase
    slug and capitalised in a comment, and both are the same leak.
    """
    wanted = [n.lower() for n in needles if n]
    found: dict[str, list[str]] = {}
    if not wanted:
        return found
    for path in files:
        if path.suffix.lower() in _BINARY:
            continue
        try:
            if path.stat().st_size > _MAX_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
        except (OSError, ValueError):
            continue
        hits = [n for n in wanted if n in text]
        if hits:
            found[str(path)] = hits
    return found


def banned_names() -> list[str]:
    """The list, from the environment or the local file, or empty."""
    raw = os.environ.get(ENV_VAR, "")
    names = [part.strip() for part in raw.split(",")]
    if LOCAL_LIST.is_file():
        for line in LOCAL_LIST.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                names.append(line)
    return [n for n in names if n]


class TestTheScannerItself:
    """Always runs. An unarmed gate still has to be a working gate."""

    def test_it_finds_a_name_that_is_really_there(self, tmp_path):
        """Ground control. Without this, a scanner that returns {} for
        everything passes the real assertion below and always will."""
        f = tmp_path / "a.py"
        f.write_text("greeting = 'hello persimmon'", encoding="utf-8")
        assert scan([f], ["persimmon"]) == {str(f): ["persimmon"]}

    def test_it_does_not_find_a_name_that_is_not(self, tmp_path):
        f = tmp_path / "a.py"
        f.write_text("greeting = 'hello world'", encoding="utf-8")
        assert scan([f], ["persimmon"]) == {}

    def test_case_does_not_hide_a_name(self, tmp_path):
        """The literal reached source as a lowercase slug in code and
        capitalised in a comment. One search has to catch both."""
        f = tmp_path / "a.py"
        f.write_text("# Persimmon, the reference" + chr(10) + "x = 'PERSIMMON'",
                     encoding="utf-8")
        assert scan([f], ["persimmon"])

    def test_an_empty_list_finds_nothing_rather_than_everything(self, tmp_path):
        f = tmp_path / "a.py"
        f.write_text("anything at all", encoding="utf-8")
        assert scan([f], []) == {}
        assert scan([f], [""]) == {}

    def test_binary_and_huge_files_are_skipped_without_raising(self, tmp_path):
        blob = tmp_path / "b.png"
        blob.write_bytes(bytes(range(256)) * 8)
        gone = tmp_path / "missing.py"
        assert scan([blob, gone], ["persimmon"]) == {}

    def test_the_tracked_set_is_where_the_scan_looks(self):
        """Positive control on the boundary: the tracked list contains this
        very file and contains nothing from .venv or .git - not because they
        are excluded by name, but because git does not track them."""
        files = tracked_files(REPO)
        assert Path(__file__).resolve() in {f.resolve() for f in files}
        parts = {part for f in files for part in f.parts}
        assert ".venv" not in parts
        assert ".git" not in parts

    def test_a_canary_in_a_tracked_file_is_found(self):
        """Positive control on the real corpus. The canary is a string that
        genuinely IS in a tracked file - this docstring - so a scan of the
        tracked set must return at least this file."""
        canary = CANARY
        found = scan(tracked_files(REPO), [canary])
        assert str(Path(__file__).resolve()) in {
            str(Path(p).resolve()) for p in found}


class TestTheTreeItself:
    def test_no_banned_name_appears_in_tracked_source(self):
        names = banned_names()
        if not names:
            pytest.skip(
                "not armed: no %s and no %s. This gate cannot carry the list "
                "it searches for - writing the name into a tracked test would "
                "be the leak it exists to catch - so it stays off until one "
                "of those two is supplied." % (ENV_VAR, LOCAL_LIST.name))
        found = scan(tracked_files(REPO), names)
        assert not found, (
            "a name from the local banned list is in tracked source:"
            + chr(10) + "  "
            + (chr(10) + "  ").join(sorted(found)))
