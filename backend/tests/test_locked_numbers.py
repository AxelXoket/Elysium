"""Numbers written into locked prose, bound to the thing they quote.

THE DEFECT THIS EXISTS FOR, and it is not hypothetical - it was found by an
audit on 31 August 2026, three times over in one document:

  * SECURITY.md said a voice-label leak was still open and that "four log
    lines print it". The leak had been closed and the content ledger emptied.
    Its registered proof - "the scan equals the ledger" - passed BECAUSE of
    the fix, not in spite of it.
  * SECURITY.md said "forty-eight places" write an exception's message. The
    ledger said fifty.
  * SECURITY.md said the gate reads "73 files". It read 75.

Every one of those documents is SHA-256 locked, and every one of those
sentences was inside the lock. The lock caught nothing, and could not: it
hashes the prose, so it fires when a human EDITS a sentence and never when
the world moves out from under one. The registered proofs caught nothing
either, and could not: they measure the mechanism, and the sentences quote
counts the mechanism produces.

So the counts are bound here. A number in a locked document is now read out
of the document and compared to the value it claims to be quoting, which
makes the failure mode structural rather than a matter of somebody
remembering. The prose is the assertion; this is what makes it one.

DELIBERATELY not a source-text test of behaviour: it reads DOCUMENTS, which
are the artefact under test, and compares them to values IMPORTED from the
ledger they describe. Nothing here retypes a count.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests import test_log_identifier_privacy as gate

ROOT = Path(__file__).resolve().parents[2]
SECURITY = ROOT / "SECURITY.md"
README = ROOT / "README.md"

#: Only what these documents actually spell out. English words rather than
#: digits, because that is how the prose is written, and a document that
#: switched to digits would fail here loudly rather than drift quietly.
WORDS = {
    0: "zero", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
    6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
    11: "eleven", 12: "twelve", 13: "thirteen", 14: "fourteen",
    15: "fifteen", 16: "sixteen", 17: "seventeen", 18: "eighteen",
    19: "nineteen", 20: "twenty", 30: "thirty", 40: "forty", 50: "fifty",
    60: "sixty", 70: "seventy", 80: "eighty", 90: "ninety",
}


def spell(n: int) -> str:
    if n in WORDS:
        return WORDS[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return f"{WORDS[tens * 10]}-{WORDS[ones]}"
    raise AssertionError(f"no spelling for {n}; the prose would need rewriting")


@pytest.fixture(scope="module")
def security() -> str:
    """The document with its line wrapping taken out.

    Prose is hard-wrapped at 78 columns, so half of these sentences have a
    newline somewhere in the middle of the phrase being matched - and which
    half changes every time a word upstream of it does. Matching against the
    wrapped text would make this gate fail for reformatting, which is the
    fastest way to teach somebody to edit the expectation instead of the
    document.
    """
    return " ".join(SECURITY.read_text(encoding="utf-8").split())


class TestTheTracebackCount:
    """The one number both documents quote, from one ledger."""

    def test_security_md_says_what_the_ledger_says(self, security) -> None:
        total = sum(gate.KNOWN_TRACEBACK_DEBT.values())
        assert total > 0, (
            "ground: the ledger is not empty, so there IS a count to quote")
        said = spell(total)
        assert f"there are {said} places that do it" in security, (
            f"SECURITY.md does not say the ledger's {total}. Search it for "
            f"'places that do it' and write '{said}'.")
        assert f"the existing {said} ship as they are" in security, (
            f"the second mention in the same paragraph still disagrees; it "
            f"must also say '{said}'")

    def test_the_readme_says_it_too(self) -> None:
        total = sum(gate.KNOWN_TRACEBACK_DEBT.values())
        said = spell(total)
        text = README.read_text(encoding="utf-8")
        assert f"there are {said} such places" in text, (
            f"README.md's Privacy Contract does not say the ledger's {total}")

    def test_the_module_count_is_the_ledger_s_own(self, security) -> None:
        said = spell(len(gate.KNOWN_TRACEBACK_DEBT))
        assert f"across {said}\nmodules" in security or \
               f"across {said} modules" in security, (
            f"SECURITY.md does not say the ledger's "
            f"{len(gate.KNOWN_TRACEBACK_DEBT)} modules")


class TestTheContentLedger:
    """An empty ledger and a sentence describing an open leak.

    This is the pair that went wrong. The assertion is deliberately in BOTH
    directions: while the ledger is empty the documents must not describe an
    open leak of this kind, and if a leak is ever accepted back the documents
    must stop saying it is closed.
    """

    def test_an_empty_ledger_is_not_described_as_an_open_leak(
            self, security) -> None:
        if gate.KNOWN_CONTENT_DEBT:
            pytest.skip("a content leak is accepted; the other test applies")
        assert "still open, and it is counted rather than" not in security, (
            "the content ledger is empty but SECURITY.md still describes a "
            "leak of that kind as open")
        assert "Four log lines print" not in security, (
            "SECURITY.md still quotes a count of leaking log lines that the "
            "ledger says is zero")

    def test_a_non_empty_ledger_is_not_described_as_closed(
            self, security) -> None:
        """POSITIVE CONTROL for the direction that is NOT true today.

        With no leak accepted, the phrase this looks for must be absent from
        the assertion path - so the test above is doing the work and this one
        is a placeholder that becomes real the day somebody accepts a leak.
        """
        if not gate.KNOWN_CONTENT_DEBT:
            assert "is now CLOSED" in security, (
                "ground: the document does claim the leak is closed, which "
                "is the claim the other test is guarding")
            return
        assert "is now CLOSED" not in security


class TestTheSweptTree:
    def test_the_file_count_in_the_prose_is_the_one_the_gate_reads(
            self, security) -> None:
        swept = len(gate._swept())
        assert swept >= gate._FILE_FLOOR, "ground: the gate is really reading"
        assert f"shipped tree - {swept}\nfiles" in security or \
               f"shipped tree - {swept} files" in security, (
            f"SECURITY.md quotes a file count the gate does not produce; it "
            f"reads {swept} files today")

    def test_the_gate_s_own_recorded_figure_is_current(self) -> None:
        """The same defect one layer down.

        The scanner's header comment records what it measured and when, and
        that figure had drifted by two files and four thousand lines. It is a
        comment, so nothing could have noticed.
        """
        source = Path(gate.__file__).read_text(encoding="utf-8")
        m = re.search(r"measured at (\d+) files on (\d{4}-\d{2}-\d{2})",
                      source)
        assert m, "the recorded measurement line is gone"
        files = int(m.group(1))
        swept = gate._swept()
        assert files == len(swept), (
            f"the header says {files} files; the sweep returns {len(swept)}. "
            f"A module was added to or removed from the shipped tree - "
            f"update the comment and the date, and check SECURITY.md, which "
            f"quotes the same number.")
        # The LINE count is deliberately not pinned. It moves on every
        # ordinary edit, so an exact assertion would be a chore somebody
        # learns to satisfy without reading - and `_LINE_FLOOR` already
        # catches the failure that matters, a sweep that matched nothing.
        assert sum(len(src.splitlines()) for _rel, src in swept)             >= gate._LINE_FLOOR
