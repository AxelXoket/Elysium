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


MIN_CASES = 10


def _cases():
    # The floor lives HERE, not in a sibling test, because an empty corpus does
    # not fail the parametrized test below - pytest turns a zero-length param
    # set into a SKIP, and a skip reads as "fine" in every summary line. Raising
    # during collection is the only failure loud enough. A sibling guard would
    # work only for as long as nobody deleted it as a duplicate.
    data = json.loads(CORPUS.read_text(encoding="utf-8"))
    if len(data["cases"]) < MIN_CASES:
        raise AssertionError(
            f"narrative corpus has {len(data['cases'])} cases, expected at "
            f"least {MIN_CASES} - the shared contract is asserting nothing")
    return [pytest.param(c, id=c["name"]) for c in data["cases"]]


@pytest.mark.parametrize("case", _cases())
def test_narration_spans_match_the_shared_corpus(case):
    spans = [s.text for s in sp._scan_emphasis(case["text"]) if s.em]
    assert spans == case["em"]
