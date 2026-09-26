"""The prompt sanitizer (synthesis 4.4, Ek O 5): vectors, invariants, bounds,
the shipped table, and the cache.

Every expected output comes from shared/sanitizer_vectors.json, which the
audited reference implementation (audit/prompt-research/sanitizer_ref.py)
computed; the TypeScript mirror is held to the same file. Nothing here
retypes a rule or an output.

Lanes: everything in this file is the gate lane (deterministic, operation
counts, no wall clock). Wall clock and memory budgets (ST-10b) are the
periodic lane and live in the reference script.
"""

from __future__ import annotations

import json
import threading
import unicodedata

import pytest

import prompt_sanitizer as ps

VECTORS = json.loads(ps.shared_path("sanitizer_vectors.json").read_text(encoding="utf-8"))


def _cases(section):
    return [pytest.param(v, id=f"{section}-{i}") for i, v in enumerate(VECTORS[section])]


# ── the vector file is the reference set, and the constants agree ──────────

def test_the_vector_file_is_the_full_reference_set():
    assert (len(VECTORS["attack"]), len(VECTORS["clean"]), len(VECTORS["names"]),
            len(VECTORS["known_pass"]), len(VECTORS["gate_inputs"])) == (111, 35, 14, 7, 9)
    assert len(VECTORS["fuzz"]) == 600


def test_constants_are_the_ones_the_vectors_were_made_with():
    c = VECTORS["constants"]
    assert (ps.NAME_MAX, ps.NAME_PRECUT, ps.FIELD_MAX, ps.OOC_REPL, ps.LT, ps.GT,
            ps.DEFAULT_NAME, ps.EXPANSION_MAX) == (
        c["name_max"], c["name_precut"], c["field_max"], c["ooc_replacement"],
        c["lt"], c["gt"], c["default_name"], c["expansion_max"])
    assert ps.RULES_VERSION == VECTORS["rules_version"] == ps.TABLE["rules_version"]
    assert ps.TABLE["unicode_version"] == VECTORS["unicode_version"]


# ── ST-7 ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("v", _cases("attack"))
def test_st7_every_attack_is_neutralised_byte_for_byte(v):
    # Positive control: the detector sees a marker in every raw vector, so an
    # empty `markers(out)` below means the sanitizer removed it.
    assert ps.markers(v["in"]), "the vector carries no marker to remove"
    out = ps._sanitize(v["in"])
    assert out == v["out"]
    assert ps.markers(out) == []
    if v["golden_replacement"]:
        assert ps.OOC_REPL in out


@pytest.mark.parametrize("v", _cases("clean"))
def test_st7_ground_control_clean_text_is_untouched(v):
    assert ps._sanitize(v["in"]) == v["in"] == v["out"]


@pytest.mark.parametrize("v", _cases("known_pass"))
def test_known_passes_stay_recorded_as_they_are(v):
    """Recorded gaps (synthesis 4.4 "Bilinen gecisler"), not fixed: a change
    here is a rules change and must update the vectors on purpose."""
    assert ps._sanitize(v["in"]) == v["out"]


# ── ST-6 names ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("v", _cases("names"))
def test_st6_names(v):
    out = ps.sanitize_name(v["in"])
    assert out == v["out"]
    assert len(out) <= ps.NAME_MAX
    assert ps.markers(out) == []
    assert not set(out) & set("[]<>{}\n")


def test_st6_ground_control_a_plain_name_is_unchanged():
    assert ps.sanitize_name("Mira") == "Mira"


def test_st6_a_megabyte_name_is_cut_before_the_work(monkeypatch):
    seen = []
    real = ps._sanitize

    def recording(text, ops=None):
        seen.append(len(text))
        return real(text, ops)

    monkeypatch.setattr(ps, "_sanitize", recording)
    out = ps.sanitize_name("\ufdfa" * (1 << 20))
    assert seen and max(seen) <= ps.NAME_PRECUT
    assert len(out) <= ps.NAME_MAX


def test_st6_the_cut_counts_code_points_not_units():
    """PK-28: 64 CODE POINTS. The TypeScript mirror pins the same result."""
    emoji = chr(0x1F600)
    assert ps.sanitize_name(emoji * 70) == emoji * ps.NAME_MAX


def test_st6_empty_or_all_bracket_names_get_the_default():
    assert ps.sanitize_name("") == ps.DEFAULT_NAME
    assert ps.sanitize_name("<[{}]>") == ps.DEFAULT_NAME


# ── ST-9 invariants ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("v", _cases("attack"))
def test_st9_invariants(v):
    y = ps._sanitize(v["in"])
    assert ps._sanitize(y) == y, "not idempotent"
    n = unicodedata.normalize("NFKC", y)
    assert ps.markers(n) == [], "a marker reappears under NFKC"
    assert ps._sanitize(n) == n, "NFKC of the output is not clean"


def test_st9_positive_control_the_detector_finds_a_raw_run():
    assert "((" in ps.markers("(((x)))")


# ── ST-8 parity with the reference, on generated input ─────────────────────

def test_st8_fuzz_matches_the_reference():
    changed = 0
    for f in VECTORS["fuzz"]:
        assert ps._sanitize(f["in"]) == f["out"], ascii(f["in"])
        assert ps.sanitize_name(f["in"]) == f["name_out"], ascii(f["in"])
        changed += f["in"] != f["out"]
    assert changed > 100, "ground: the fuzz set must exercise the rules"


# ── ST-10a: work, counted ───────────────────────────────────────────────────

KIB = 1024


def _gen(unit: str, n: int) -> str:
    return (unit * (n // len(unit) + 1))[:n]


@pytest.mark.parametrize("g", [pytest.param(g, id=g["label"]) for g in VECTORS["gate_inputs"]])
def test_st10a_operation_bounds(g):
    counts = []
    for n in (32 * KIB, 64 * KIB):
        s = _gen(g["unit"], n)
        sh = ps.shadow(ps.strip_sent(ps.fold_linebreaks(s)))[0]
        assert len(sh) <= ps.EXPANSION_MAX * n, "shadow longer than 4n"
        ops = {"close": 0}
        ps._sanitize(s, ops)
        assert ops["close"] <= 2 * n, "close-bracket searches above 2n"
        counts.append(ops["close"])
    growth = counts[1] / counts[0] if counts[0] else 0
    assert growth <= 2.2, "close searches grow faster than the input"


def test_st10a_ground_control_prose_costs_no_close_search():
    ops = {"close": 0}
    ps._sanitize("The quiet harbour town. " * 2000, ops)
    assert ops["close"] == 0


# ── the table is data, and it is what the generator makes ───────────────────

def test_the_shipped_table_is_what_the_generator_produces():
    """Drift guard. The table is generated once from a named Unicode version;
    if this interpreter has another one the comparison would be meaningless,
    and that is said rather than skipped silently."""
    assert unicodedata.unidata_version == ps.TABLE["unicode_version"], (
        "regenerate the table on purpose (python prompt_sanitizer.py "
        "--write-table) and the vectors with it")
    # ps.TABLE is what the running module loaded from the shipped file.
    assert json.loads(ps.dump_table(ps.build_table())) == ps.TABLE


def test_the_fold_round_trips_through_its_compact_form():
    fold = ps.expand_fold(ps.TABLE)
    assert len(fold) > 20000, "ground: the table is not empty"
    assert ps.compress_fold(fold) == ps.TABLE["fold"]


# ── the cache (ST-12) ───────────────────────────────────────────────────────

def test_st12_a_build_that_started_before_the_lock_is_never_stored():
    started, release = threading.Event(), threading.Event()

    def slow(text, ops=None):
        started.set()
        release.wait(5)
        return ps._sanitize(text)

    cache = ps.SanitizeCache(fn=slow)
    result = {}
    th = threading.Thread(target=lambda: result.setdefault(
        "v", cache.get("secret card text (OOC: x)")))
    th.start()
    assert started.wait(5)
    cache.lock()                      # the vault locks while the build runs
    release.set()
    th.join(5)
    assert result["v"].startswith("secret"), "the request still gets its text"
    assert len(cache) == 0, "decrypted text survived the lock"


def test_st12_ground_control_without_a_lock_the_entry_is_kept():
    cache = ps.SanitizeCache()
    cache.get("kept")
    assert len(cache) == 1


def test_st12_unlock_makes_the_cache_writable_again():
    cache = ps.SanitizeCache()
    cache.get("x")
    cache.lock()
    assert len(cache) == 0
    cache.get("while locked")
    assert len(cache) == 0, "stored while locked"
    cache.unlock()
    cache.get("after unlock")
    assert len(cache) == 1


def test_st12_an_oversize_field_is_refused_before_any_work():
    calls = []
    cache = ps.SanitizeCache(fn=lambda t, ops=None: calls.append(t) or t)
    with pytest.raises(ps.PromptFieldTooLarge):
        cache.get("x" * (ps.FIELD_MAX + 1))
    assert calls == []
    assert cache.get("x" * ps.FIELD_MAX) == "x" * ps.FIELD_MAX


def test_the_cache_is_bounded_by_entries():
    cache = ps.SanitizeCache(max_entries=3)
    for i in range(10):
        cache.get(f"entry {i}")
    assert len(cache) == 3


def test_sanitize_field_serves_a_repeat_from_the_cache(monkeypatch):
    calls = []
    real = ps._sanitize
    cache = ps.SanitizeCache(fn=lambda t, ops=None: calls.append(t) or real(t))
    monkeypatch.setattr(ps, "CACHE", cache)
    assert ps.sanitize_field("(OOC: once)") == ps.sanitize_field("(OOC: once)")
    assert len(calls) == 1


# ── strip_space: one whitespace set on both sides (PK-10) ─────────────────

def test_strip_space_uses_pythons_whitespace_set():
    assert ps.strip_space("\x85\x1fa\x1f") == "a"
    # U+FEFF is not whitespace to Python; JS trim() would cut it.
    assert ps.strip_space("\ufeffa") == "\ufeffa"
