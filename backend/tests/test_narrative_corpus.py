"""The Python half of the shared narration contract.

`shared/narrative_corpus.json` is asserted by BOTH suites - this file and
`frontend/src/test/lib/narrativeCorpus.test.ts`. If the two scanners ever
disagree about what counts as narration, one of them goes red here rather than
producing a message whose italics and whose voice tell different stories.
"""
import json
from pathlib import Path

import pytest

import speech_prep as sp

CORPUS = Path(__file__).resolve().parents[2] / "shared" / "narrative_corpus.json"


def _cases():
    data = json.loads(CORPUS.read_text(encoding="utf-8"))
    return [pytest.param(c, id=c["name"]) for c in data["cases"]]


def test_corpus_file_is_present_and_not_empty():
    # A silently missing corpus would turn every case below into zero tests,
    # and the contract would be "passing" while asserting nothing.
    data = json.loads(CORPUS.read_text(encoding="utf-8"))
    assert len(data["cases"]) >= 10


@pytest.mark.parametrize("case", _cases())
def test_narration_spans_match_the_shared_corpus(case):
    spans = [s.text for s in sp._scan_emphasis(case["text"]) if s.em]
    assert spans == case["em"]
