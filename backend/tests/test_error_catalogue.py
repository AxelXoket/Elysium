"""The three-way gate over shared/error_catalogue.json.

Direction (a): every code the backend can emit has a record.
Direction (c): every record has a backend producer, or a written reason why not.
Plus: the enumerator that answers (a) is not silently empty, and every error
site in the tree resolves to something a human can read.

Direction (b), that every record has a sentence, lives on the other side of the
wall in frontend/src/test/lib/errorCatalogue.test.ts, because that is where the
sentences are.

WHY THIS IS NOT A SOURCE SCAN IN DISGUISE, SAID OUT LOUD

Two of these tests read backend source through `ast.parse`. The house rule bans
tests that scan source text AS A SUBSTITUTE FOR BEHAVIOUR, and this is not
that: nothing here asserts what the app does. It answers a different question,
"what strings can this codebase place in a detail field", which is a
completeness census over a vocabulary and has no behavioural form. A behaviour
test can only ever see the union of codes that some test happened to fire, and
a full suite run constructs well under half of them. The behavioural half of
this contract is the existing suite, which is untouched.

The runtime alternative was designed twice and rejected on a measurement:
`routers/completions.py` wraps its whole streaming generator in a bare
`except Exception` that answers `internal_error`. An assertion raised from
inside the emit path is caught there and rewritten into a catalogued code, so
the alarm would arrive as a green record.
"""

from __future__ import annotations

import json
from pathlib import Path

from error_enumeration import (
    DECLARED_ALPHABETS,
    all_codes,
    declared_emissions,
    scan,
)

CATALOGUE = Path(__file__).resolve().parents[2] / "shared" / "error_catalogue.json"
_RAW = json.loads(CATALOGUE.read_text(encoding="utf-8"))
RECORDS = {r["code"]: r for r in _RAW["codes"]}

#: A floor, not a target. The regex this replaces found 51 literal codes on
#: 2026-08-10; if the walk ever finds fewer than that it has broken, whatever
#: else it reports. Same idea as the count guard in test_narrative_corpus.py.
_LITERAL_FLOOR = 51


def test_the_catalogue_is_present_and_not_empty() -> None:
    """A deleted or emptied file must not make everything below vacuous.

    Hard failure rather than a skip, deliberately. Both tests this replaces
    skip when their target is missing, and a gate that skips is a gate that
    proves nothing while looking green - which is how test_release_tree.py sat
    dark for months and how the privacy grep suite once passed having opened no
    application code at all.
    """
    assert len(RECORDS) >= 100, f"only {len(RECORDS)} records - is the file real?"


def test_the_enumerator_is_not_silently_empty() -> None:
    """The census must actually reach code, and reach all five exits.

    Six named probes, one per shape, because a count alone cannot tell a
    working walk from one pointed at the wrong directory. That is exactly the
    failure test_verify_gate.py exists to record: seventeen privacy assertions
    printed PASS having scanned nothing but their own folder.
    """
    emissions, _ = scan()
    literals = {e.code for e in emissions if e.status is not None}
    assert len(literals) >= _LITERAL_FLOOR, (
        f"only {len(literals)} literal codes found, expected at least "
        f"{_LITERAL_FLOOR}: the AST walk is broken or pointed somewhere else"
    )

    everything = emissions | declared_emissions()
    for probe, channel in (
        ("chat_not_found", "http"),              # a plain router literal
        ("tts_model_unknown", "http"),           # a module-constant detail
        ("openrouter_rate_limited", "http"),     # the completions relay alphabet
        ("proxy_unreachable", "http"),           # the proxy_health alphabet
        ("exchange_stale", "sse"),               # the completions stream
        ("image_output_rejected", "notice"),     # the notice channel
    ):
        assert any(e.code == probe and e.channel == channel for e in everything), (
            f"{probe} was not found on the {channel} channel: that whole shape "
            f"is going unread"
        )


def test_every_error_site_resolves_to_a_declared_alphabet() -> None:
    """The forcing function. An unreadable site is a failure, never a skip.

    This one inversion is the entire design. The regex that came before did not
    fail on a computed detail; it produced nothing, so a blind spot and a clean
    tree looked identical. Twelve relayed codes lived in that gap for months.
    """
    _, unresolved = scan()
    orphaned = [u for u in unresolved
                if (u.file, u.source) not in DECLARED_ALPHABETS]
    if not orphaned:
        return

    lines = ["", "this error site builds its code at runtime and nothing "
                 "declares what it can be:", ""]
    for site in orphaned:
        lines.append(f"  {site.file}:{site.line}  (channel: {site.channel})")
        lines.append(f"      {site.source}")
    lines += [
        "",
        "A code nobody can enumerate is a code nobody writes a sentence for,",
        "and it reaches the reader as \"Something went wrong. Please try",
        "again.\" That is how proxy_health.py and models_router.py relayed",
        "twelve codes that no test in this repository had ever counted.",
        "",
        "Two ways out, both one line:",
        "  1) make the detail a literal or a module-level constant, or",
        "  2) declare the alphabet in the module that OWNS the site:",
        "       FOO_REASONS: frozenset[str] = frozenset({\"...\"})",
        "     and register it in backend/tests/error_enumeration.py:",
        "       (\"routers/foo.py\", \"exc.reason\"): \"foo:FOO_REASONS\",",
    ]
    raise AssertionError("\n".join(lines))


def test_every_code_the_backend_can_emit_is_catalogued() -> None:
    """Direction (a)."""
    missing = sorted(all_codes() - set(RECORDS))
    if not missing:
        return

    emissions, _ = scan()
    where = {}
    for e in emissions | declared_emissions():
        where.setdefault(e.code, f"{e.file}:{e.line}, {e.channel}")

    lines = ["", "the backend can emit these codes and the catalogue has no "
                 "record:", ""]
    for code in missing:
        lines.append(f"  {code}  ({where.get(code, 'a declared alphabet')})")
    lines += [
        "",
        "Add each to shared/error_catalogue.json, sorted by code:",
        '  { "code": "...", "statuses": [409], "channels": ["http"] }',
        "",
        "Then errorMessages.ts needs a sentence that tells the reader what to",
        "DO. Until both exist this code renders as \"Something went wrong.",
        "Please try again.\" and the UI cannot tell it from a code we have",
        "never heard of.",
    ]
    raise AssertionError("\n".join(lines))


def test_the_catalogue_has_no_orphan_record() -> None:
    """Direction (c), backend half."""
    emitted = all_codes()
    orphans = sorted(
        code for code, rec in RECORDS.items()
        if code not in emitted and not rec.get("no_backend_producer")
    )
    if not orphans:
        return
    raise AssertionError(
        "\n" + "\n".join([
            "the catalogue declares these and no backend path can produce them:",
            "",
            *(f"  {c}" for c in orphans),
            "",
            "A record nobody emits is usually a code somebody renamed. Delete",
            "it, together with its sentence in errorMessages.ts. If the",
            "backend genuinely never produces it, say why in one field:",
            '  "no_backend_producer": "synthesised by parseApiError.ts:77"',
        ])
    )


def test_an_escape_hatch_must_carry_a_written_reason() -> None:
    """The hatch is the part that decays, so it is the part with a rule.

    A boolean flag would be one keystroke; a required sentence is a line a
    reviewer reads. Three records use it today, all three because the frontend
    synthesises the code itself.
    """
    empty = sorted(
        code for code, rec in RECORDS.items()
        if "no_backend_producer" in rec
        and len(str(rec["no_backend_producer"]).strip()) < 40
    )
    assert not empty, (
        f"these exemptions have no real reason written on them: {empty}. "
        f"An exemption without a reason is a hole with a comment next to it."
    )


def test_the_catalogue_declares_the_statuses_the_routers_actually_raise() -> None:
    """`statuses` is machine-checked in both directions, so it cannot rot.

    Only literal raise sites contribute. A code produced solely at a computed
    site carries [], which is a fact about how it reaches the client and not a
    gap in the record.
    """
    emissions, _ = scan()
    actual: dict[str, set[int]] = {}
    for e in emissions:
        if e.status is not None:
            actual.setdefault(e.code, set()).add(e.status)

    wrong = []
    for code, rec in RECORDS.items():
        declared = set(rec.get("statuses", []))
        found = actual.get(code, set())
        if declared != found:
            wrong.append(f"  {code}: catalogue says {sorted(declared)}, "
                         f"routers raise {sorted(found)}")
    assert not wrong, "\n" + "\n".join(["statuses disagree with the tree:", *wrong])


def test_the_catalogue_declares_the_channels_the_code_travels_on() -> None:
    """Same treatment for `channels`.

    This is the field that would have made the three missing notice sentences
    obvious a long time ago: all three are notice-only, and nothing in the repo
    recorded that the notice channel carried user-facing codes at all.
    """
    everything = scan()[0] | declared_emissions()
    actual: dict[str, set[str]] = {}
    for e in everything:
        actual.setdefault(e.code, set()).add(e.channel)

    wrong = []
    for code, rec in RECORDS.items():
        declared = set(rec.get("channels", []))
        found = actual.get(code, set())
        if declared != found:
            wrong.append(f"  {code}: catalogue says {sorted(declared)}, "
                         f"tree emits on {sorted(found)}")
    assert not wrong, "\n" + "\n".join(["channels disagree with the tree:", *wrong])


def test_every_declared_alphabet_still_has_a_site() -> None:
    """A registration whose site is gone must be noticed, not carried.

    The mirror of the forcing function, and the only pressure that ever makes
    the registration table shorter. Same reasoning as the hygiene gate's stale
    waiver check: an exemption that outlives the thing it excused is how a list
    of exceptions only ever grows.
    """
    _, unresolved = scan()
    live = {(u.file, u.source) for u in unresolved}
    stale = sorted(k for k in DECLARED_ALPHABETS if k not in live)
    assert not stale, (
        f"these registrations in error_enumeration.py match no site any more: "
        f"{stale}. The code was made a literal, or moved, or deleted. Remove "
        f"the entry."
    )


def test_the_voice_funnel_can_only_produce_codes_it_declares() -> None:
    """The alphabet declaration for the voice channel has to be TRUE.

    `error_enumeration.DECLARED_ALPHABETS` claims the two voice sites draw from
    `tts.errors.ALL_CODES`. Until 2026-08-10 that claim was false: `_code_for`
    accepted any string starting with `tts_`, so an exception carrying
    `tts_banana` sent `tts_banana` to the client, which has no sentence and
    renders the generic fallback.

    A declaration nobody checks is the same shape of defect as the regex this
    whole catalogue replaces: it looks like coverage and asserts nothing. This
    is a behaviour test on the funnel itself, not a reading of it.
    """
    from tts.errors import ALL_CODES, TTS_SYNTHESIS_FAILED, TTS_OUT_OF_MEMORY
    from tts.stream_hook import _code_for

    class _Carrying(Exception):
        def __init__(self, code): self.code = code

    # A real code passes through untouched.
    assert _code_for(_Carrying(TTS_OUT_OF_MEMORY)) == TTS_OUT_OF_MEMORY

    # A tts_-prefixed string that is not a real code does NOT.
    assert _code_for(_Carrying("tts_banana")) == TTS_SYNTHESIS_FAILED

    # Neither does anything else.
    assert _code_for(_Carrying("chat_not_found")) == TTS_SYNTHESIS_FAILED
    assert _code_for(_Carrying(None)) == TTS_SYNTHESIS_FAILED
    assert _code_for(Exception("no code attribute at all")) == TTS_SYNTHESIS_FAILED

    # And whatever it returns is always something the catalogue knows.
    assert TTS_SYNTHESIS_FAILED in ALL_CODES


class TestTheWalkHappensOncePerProcess:
    """The cache added on 2026-08-10, and the two things that could go wrong.

    Ten callers wanted `scan()` during one run: the six below, plus
    `declared_emissions` and `all_codes`, which each call it again on their
    own. Every one of them parsed every .py file under backend/ to re-derive a
    constant, at roughly seven seconds of suite time.

    Both tests here are behaviour tests on the cache itself, which is a real
    behaviour and not an implementation detail: the first says the work is not
    repeated, the second says the saving did not buy a way for one caller to
    corrupt another's answer.
    """

    def test_the_tree_is_parsed_once_no_matter_how_many_callers_ask(self):
        import error_enumeration as ee

        calls = []
        real_files = ee._files

        def counting_files():
            calls.append(1)
            return real_files()

        ee._scan_once.cache_clear()
        ee._files = counting_files
        try:
            first, _ = ee.scan()
            second, _ = ee.scan()
            ee.all_codes()
        finally:
            ee._files = real_files

        assert len(calls) == 1, (
            f"the tree was walked {len(calls)} times for three callers; the "
            f"cache on _scan_once is not holding"
        )
        assert first == second, "two calls disagreed about the same tree"

    def test_one_caller_cannot_poison_the_next_ones_answer(self):
        """The reason the cached value is frozen rather than copied.

        A cache that hands the same mutable set to everyone turns a caller's
        local edit into everyone else's data. Nothing in this suite does that
        today, which is exactly the kind of fact that stops being true without
        anyone noticing, so the container makes it impossible instead.
        """
        import error_enumeration as ee

        emissions, unresolved = ee.scan()
        before = len(emissions)
        emissions.clear()
        unresolved.clear()

        again, again_unresolved = ee.scan()
        assert len(again) == before, "clearing one caller's set emptied the cache"
        assert again_unresolved, "clearing one caller's list emptied the cache"
