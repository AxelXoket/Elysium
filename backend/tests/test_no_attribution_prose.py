"""Nothing in this repository says who told the author what to do.

Every commit here goes out under one name, to a public remote. Prose that
names a second party - "the owner's decision", "on the owner's instruction",
"the owner said" - leaves a permanent, uncorrectable record of a
conversation rather than a record of the software. A hundred and twenty
seven such phrases were found and removed on 2 September 2026, across
seventy nine files; without a gate the count starts climbing again the next
time somebody writes down why a default is what it is.

SO THE RULE IS MECHANICAL, not remembered. Say WHAT was decided and WHY.
Do not say WHO decided it.

    "Owner's decision, 8 August 2026."   ->  "Decided 8 August 2026."
    "the owner's rule is that a note     ->  "a note never disappears"
     never disappears"
    "the strict half the owner asked     ->  "the strict half"
     for"

DELIBERATELY UNDER-INCLUSIVE, and that is the honest trade. It matches the
shapes that actually occurred rather than trying to reason about English:
a possessive followed by a decision noun, `owner` followed by a decision
verb, and a handful of fixed phrases. Somebody determined to write an
attribution can still slip one past it. What it stops is the ordinary case,
which is the case that produced all hundred and twenty seven.

THE WORD ITSELF IS NOT BANNED. `owner` is a precise technical term in three
places this codebase cares about - a Windows ACL owner and the OWNER RIGHTS
SID, the process that owns a lock, and the `boundaries.owner` column - and
those uses are correct. The pattern below matches a PERSON who ruled,
instructed or asked, and leaves the rest alone. That is why it is a phrase
list rather than a word list.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]

#: A person who decided, instructed or owns. Deliberately phrase-shaped: a
#: bare `owner` is usually the ACL sense and banning it would make this gate
#: a nuisance somebody switches off.
ATTRIBUTION = re.compile(
    # "the owner's rule", "Owner's decision", "the owner's own vault"
    r"\bowner'?s\s+(ruling|decision|call|instruction|answer|rule"
    r"|choice|requirement|own|real|personal|machine|desktop|brief|word"
    r"|karar)\b"
    # "the owner said", "the owner chose", "what the owner typed"
    r"|\bowner\s+(said|asked|chose|added|wants|takes|kept|needs|typed"
    r"|set|suspended|should|made|reads|described|reported|removed"
    r"|confirmed|complained|named|runs|saw|explicitly)\b"
    # the fixed phrases, in both languages
    r"|\bper\s+the\s+owner\b|\bfrom\s+the\s+owner\s+directly\b"
    r"|\bon\s+(the\s+)?owner'?s?\s+instruction"
    r"|\bsahibin\s+karar|\bsahip\s+karar"
    r"|\bwe\s+were\s+told\b|\bas\s+instructed\b",
    re.I)

_SKIP_DIRS = ("frontend/node_modules/", "assets/", "docs/")
_SKIP_EXT = (".png", ".exe", ".drawio", ".pdf", ".lock", ".ico", ".wav",
             ".jpg", ".jpeg", ".webp", ".svg", ".woff", ".woff2", ".ttf")

#: Below this the sweep is not reading the repository and a clean result
#: means nothing. Calibrated at 645 tracked files on 2 September 2026.
_FILE_FLOOR = 400


#: THIS FILE, and it is the only exemption there is.
#:
#: A gate that forbids a phrase has to write that phrase down: the pattern
#: quotes it, the ground control plants it, and the docstring shows what to
#: write instead. Scanning itself, it reports its own examples and refuses
#: every commit forever. Excluded by exact path so the hole is one file
#: wide and cannot be widened by accident, and named here rather than
#: buried in a skip list.
_THIS_FILE = "backend/tests/test_no_attribution_prose.py"


def _tracked() -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=_ROOT, capture_output=True,
                         text=True, encoding="utf-8", errors="replace")
    if out.returncode != 0:                              # pragma: no cover
        pytest.skip("not a git checkout")
    return [_ROOT / f for f in out.stdout.split()
            if not f.startswith(_SKIP_DIRS) and not f.endswith(_SKIP_EXT)
            and f != _THIS_FILE]


def _hits(paths) -> list[str]:
    found = []
    for p in paths:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:                                  # pragma: no cover
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if ATTRIBUTION.search(line):
                try:
                    rel = p.relative_to(_ROOT).as_posix()
                except ValueError:
                    rel = p.name        # a scratch file from a control
                found.append(f"{rel}:{i}\n      {line.strip()[:100]}")
    return found


class TestNobodyIsQuotedInThisRepository:
    def test_no_file_names_a_person_who_decided(self) -> None:
        paths = _tracked()
        assert len(paths) >= _FILE_FLOOR, (
            f"the sweep found only {len(paths)} files, which is below the "
            f"floor of {_FILE_FLOOR}. A clean result from a sweep that read "
            f"almost nothing is not a clean result.")

        found = _hits(paths)

        assert found == [], (
            f"{len(found)} line(s) name a person who decided or instructed. "
            f"Say WHAT was decided and WHY, not WHO decided it:\n    "
            + "\n    ".join(found))

    def test_the_exemption_is_one_file_wide(self) -> None:
        """POSITIVE CONTROL for the hole this file cuts in itself.

        An exclusion is the classic way a gate stops meaning anything, so
        the size of it is asserted rather than trusted: exactly one path,
        and it has to be this one.
        """
        swept = {p.relative_to(_ROOT).as_posix() for p in _tracked()}
        assert _THIS_FILE not in swept, "the exemption is not in effect"
        assert Path(_ROOT / _THIS_FILE).exists(), (
            "the exempted path does not exist, so the exemption is a typo "
            "and something else is silently being scanned or skipped")
        # Every OTHER test file in the same directory is still swept.
        siblings = {p for p in swept
                    if p.startswith("backend/tests/test_")}
        assert len(siblings) > 100, (
            f"only {len(siblings)} test files reached the sweep; the "
            f"exclusion is wider than one file")

    def test_the_detector_fires(self, tmp_path) -> None:
        """GROUND CONTROL. An empty result and a broken regex look identical
        from the outside, and this file's whole value is the empty result."""
        sample = tmp_path / "sample.py"
        sample.write_text(
            "# Owner's decision, 8 August 2026: report was not enough.\n"
            "# The rule is that a note never disappears.\n",
            encoding="utf-8")
        found = _hits([sample])
        assert len(found) == 1, f"expected exactly the first line, got {found}"
        assert "Owner's decision" in found[0]

    @pytest.mark.parametrize("line", [
        "#: The OWNER RIGHTS well known SID. Without an ACE for it",
        '"the folder is still reachable by a group that is not the owner"',
        "owner = getattr(module, node.value.id, None)",
        "-- Scope and owner say the same thing, or the row is a lie",
        "# Passive: this is a viewer, not the owner of the setting.",
        "the lock owner is a process id, not a person",
    ])
    def test_the_technical_sense_is_not_touched(self, tmp_path, line) -> None:
        """POSITIVE CONTROL, and the reason this is a phrase list.

        A gate that banned the word would have deleted a Windows SID name, a
        schema column and an AST-walk variable. Every line here is real, from
        this repository, and every one of them must pass.
        """
        sample = tmp_path / "sample.py"
        sample.write_text(line + "\n", encoding="utf-8")
        assert _hits([sample]) == [], f"the technical sense was flagged: {line}"
