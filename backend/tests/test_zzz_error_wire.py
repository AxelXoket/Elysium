"""The wire gate: what actually reached a client has to be in the catalogue.

Named `test_zzz_` so it collects last. pytest walks a flat directory in
filename order and this file has to run after everything else has had its
chance to emit. There is no ordering plugin in requirements.txt and one is not
worth adding for a single file; if the assumption ever breaks, the fix is a
`pytest_collection_modifyitems` hook in conftest, not a dependency.

WHAT THIS CATCHES THAT THE STATIC CENSUS CANNOT

`error_enumeration.py` reads the source, which makes it the more complete of
the two: it sees paths no test executes. But eleven of its sites build their
code at runtime, and for those it reads a human's written declaration of which
alphabet the site draws from. It cannot check that declaration, because the
declaration is its input.

On 2026-08-10 one of those declarations was false. The two voice sites were
registered as drawing from `tts.errors.ALL_CODES` while `_code_for` accepted
any string beginning `tts_`. The census was green and a person caught it by
reading the funnel. This is the test that catches the next one: a code that
reaches the wire and is not in the catalogue fails here, whatever the source
was believed to say.

WHAT IT DELIBERATELY DOES NOT ASSERT

The reverse direction. "Every catalogued code was observed" would be red on day
one for every code no test happens to fire, and the only way to quiet it would
be to mark those exempt, which is precisely the hole the catalogue exists to
close. Completeness is the static census's job; this is the honesty check on
its inputs.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.error_wire_recorder import RECORDER

CATALOGUE = Path(__file__).resolve().parents[2] / "shared" / "error_catalogue.json"
RECORDS = {r["code"]: r for r in
           json.loads(CATALOGUE.read_text(encoding="utf-8"))["codes"]}

#: Measured, never guessed, and measured twice by two methods that agreed.
#:
#: An audit of every `assert` in backend/tests/ found 58 distinct catalogued
#: codes named in one, and a code named in an assertion had to cross the wire
#: to get there. A full run of this suite then observed 59. The floor is the
#: predicted 58 rather than the observed 59, so that deleting a single error
#: test does not turn this red for a reason that has nothing to do with the
#: gate. The margin is one; if it is ever exceeded, re-measure rather than
#: nudging the number down.
#:
#: Worth reading twice: 59 of 102. The suite exercises well under two thirds of
#: the vocabulary, which is the whole reason this file is a companion to the
#: static census and not a replacement for it.
#:
#: The floor is the whole reason this file is not decoration: the assertion
#: below is a SUBSET check, and a subset check over an empty set passes. A
#: renamed fixture, a moved conftest, or an `install()` that silently stopped
#: running would all turn this gate green while proving nothing. That is the
#: exact failure this repository has already had twice.
#:
#: RE-MEASURED 2026-08-11, KADEME 16a. The margin above was one, and the test
#: rewrite has been moving, folding and deleting tests across the whole suite,
#: which is precisely the traffic this number rides on. A full run now observes
#: 62, not 59. The floor stays at 58 rather than rising to match: it is the
#: count of codes NAMED IN ASSERTIONS, which is the thing that has to be true,
#: while the observed figure also counts codes a test crossed on its way
#: somewhere else. Raising the floor to the observed number would make every
#: future deletion of an unrelated test a red gate here.
_OBSERVED_FLOOR = 58


def test_the_recorder_observed_a_realistic_amount_of_traffic() -> None:
    observed = RECORDER.codes()
    assert len(observed) >= _OBSERVED_FLOOR, (
        f"only {len(observed)} distinct error codes crossed the wire this "
        f"session, and at least {_OBSERVED_FLOOR} are named in assertions in "
        f"this suite. Either the recorder is not installed (check that "
        f"conftest.py still calls error_wire_recorder.install()), or this run "
        f"was a subset of the suite, in which case this file is measuring "
        f"nothing and should not be trusted.\nobserved: {sorted(observed)}"
    )


def test_every_code_seen_on_the_wire_is_in_the_catalogue() -> None:
    unknown = sorted(RECORDER.codes() - set(RECORDS))
    if not unknown:
        return
    where = {o.code: o.channel for o in RECORDER.seen}
    raise AssertionError("\n".join([
        "",
        "these codes reached a client and the catalogue has no record:",
        "",
        *(f"  {c}  (on the {where.get(c, '?')} channel)" for c in unknown),
        "",
        "The static census did not catch this, which means one of two things.",
        "Either a new emit site exists in a shape error_enumeration.py does",
        "not know how to read, or one of its DECLARED_ALPHABETS entries is",
        "false and the site produces something outside the alphabet it was",
        "registered against. The second is what happened on 2026-08-10 to the",
        "voice funnel, and it is the reason this file exists.",
    ]))


def test_the_channel_a_code_arrived_on_is_the_channel_it_claims() -> None:
    """A code catalogued as http-only that turns up in an SSE event is a
    record that describes something other than the running system."""
    wrong = []
    for code, channel in sorted(RECORDER.pairs()):
        record = RECORDS.get(code)
        if record is None:
            continue          # covered by the test above, not twice here
        declared = set(record.get("channels", []))
        if channel not in declared:
            wrong.append(f"  {code} arrived on {channel}, catalogue says "
                         f"{sorted(declared) or 'no channel at all'}")
    assert not wrong, "\n" + "\n".join([
        "a code arrived on a channel its record does not list:", *wrong])
