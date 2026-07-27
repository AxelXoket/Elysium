"""Audit regression: the privacy grep gate must scan the APPLICATION.

The verify scripts moved from backend/ into backend/verify/ (commit d8da7db)
and their `BACKEND_DIR = dirname(abspath(__file__))` did not. The P-01..P-20
suite therefore walked its own directory: 12 files, every one of them in its own
VERIFY_FILES exclusion set. Seventeen privacy assertions reported PASS having
opened no application code at all - adding `allow_origins=["*"]` to main.py or
`logger.info("key=%s", api_key)` to secrets_service.py still printed PASS.

A gate that is green by vacuity is worse than no gate, so this pins the root it
walks rather than any individual check.
"""

import re
from pathlib import Path

import pytest

VERIFY_DIR = Path(__file__).resolve().parent.parent / "verify"
BACKEND_ROOT = VERIFY_DIR.parent

SCRIPTS = sorted(VERIFY_DIR.glob("verify_*.py"))


def test_there_are_verify_scripts_to_check():
    assert SCRIPTS, "no verify scripts found - the glob or the layout moved"


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_backend_dir_points_at_the_backend_tree(script: Path):
    source = script.read_text(encoding="utf-8")
    match = re.search(r"^BACKEND_DIR = (.+)$", source, re.MULTILINE)
    if match is None:
        pytest.skip(f"{script.name} does not define BACKEND_DIR")
    expression = match.group(1)
    resolved = eval(  # noqa: S307 - a literal from our own tree
        expression, {"os": __import__("os")}, {"__file__": str(script)},
    )
    assert Path(resolved).resolve() == BACKEND_ROOT, (
        f"{script.name} would scan {resolved}, not the backend source tree"
    )


def _aggregate_source() -> str:
    return (VERIFY_DIR / "verify_elysium_full.py").read_text(encoding="utf-8")


def _aggregated_names() -> list[str]:
    """Run the runner's own selection, without running the scripts.

    Importing the module would execute the entire suite as a side effect, so
    the list is rebuilt from its two inputs: the glob and the exclusion set.
    If either changes shape this stops matching and the tests below say so.
    """
    source = _aggregate_source()
    match = re.search(r"NOT_AGGREGATED = \{(.*?)\}", source, re.DOTALL)
    assert match, "NOT_AGGREGATED moved - update this test with it"
    excluded = set(re.findall(r'"([^"]+)"', match.group(1)))
    return sorted(p.name for p in SCRIPTS if p.name not in excluded)


def test_every_aggregated_script_exists_on_disk():
    """The gap that let the list rot: nothing compared it to the directory.

    The hand-written list named six files and the tree held twelve, so five
    phase suites were aggregated by nothing - and because the runner reported
    only on names it already knew, no output ever mentioned the missing five.
    """
    names = _aggregated_names()
    assert names, "the aggregate runs no scripts at all"
    for name in names:
        assert (VERIFY_DIR / name).is_file(), f"{name} is aggregated but absent"


def test_the_aggregate_covers_the_whole_verify_directory():
    """Every verify script is either aggregated or excluded ON PURPOSE."""
    source = _aggregate_source()
    match = re.search(r"NOT_AGGREGATED = \{(.*?)\}", source, re.DOTALL)
    assert match
    excluded = set(re.findall(r'"([^"]+)"', match.group(1)))
    on_disk = {p.name for p in SCRIPTS}
    unaccounted = on_disk - set(_aggregated_names()) - excluded
    assert not unaccounted, (
        f"{sorted(unaccounted)} are neither aggregated nor deliberately excluded"
    )
    # The two exclusions are load-bearing, so they are pinned by name: this
    # file would recurse, and the latency script needs a GPU and a model.
    assert excluded == {"verify_elysium_full.py", "verify_tts_latency.py"}
    for name in excluded:
        assert (VERIFY_DIR / name).is_file(), f"{name} is excluded but absent"


def test_the_phase_suites_are_actually_aggregated():
    """The five that were silently dropped, named so a re-drop is loud."""
    names = set(_aggregated_names())
    for required in ("verify_phase1.py", "verify_phase2.py", "verify_phase3.py",
                     "verify_phase4.py", "verify_phase5a.py"):
        assert required in names, f"{required} is back outside the gate"


def test_scripts_are_launched_from_the_directory_they_live_in():
    """The Part 1 mirror of the BACKEND_DIR bug above.

    The launcher joined BACKEND_DIR, so every path resolved to
    backend/verify_part_a.py - absent - and all six reported FILE NOT FOUND.
    A gate that is red by vacuity fails the same way a green one does: it
    stops carrying information.
    """
    source = _aggregate_source()
    assert "os.path.join(VERIFY_DIR, script)" in source, (
        "scripts are not resolved against the directory that holds them"
    )
    assert "VERIFY_DIR = os.path.dirname(os.path.abspath(__file__))" in source


def test_the_dead_part_f_branch_is_gone():
    """It tested a name that was not in the list and not on disk, so it could
    never run. A skip branch for a file nobody has is a comment pretending to
    be code."""
    source = _aggregate_source()
    assert 'script == "verify_part_f.py"' not in source
    assert "Part F deferred" not in source


def test_no_hand_written_verify_filename_can_go_stale():
    """Both filename sets are derived from the directory, not typed.

    The exclusion set used by the privacy checks had rotted unnoticed - it
    named a file that never existed and missed two that do - precisely because
    nothing compared it to disk. A name that must be maintained by hand in two
    places is a name that will disagree with itself.
    """
    source = _aggregate_source()
    assert 'glob.glob(os.path.join(VERIFY_DIR, "verify_*.py"))' in source
    # verify_part_f may survive only as prose explaining why it is gone.
    code = [ln for ln in source.splitlines()
            if not ln.strip().startswith("#") and "verify_part_f" in ln]
    assert not code, f"verify_part_f still appears in code: {code}"


def test_one_wedged_script_does_not_lose_the_others():
    source = _aggregate_source()
    assert "except subprocess.TimeoutExpired:" in source, (
        "a timeout would abort the run and discard the remaining results"
    )


def _scanned_files() -> list[Path]:
    """Reproduce verify_elysium_full.py's file collection."""
    import os

    source = (VERIFY_DIR / "verify_elysium_full.py").read_text(encoding="utf-8")
    match = re.search(r"dirs\[:\] = \[d for d in dirs\s*\n?\s*if d not in \((.*?)\)\]",
                      source, re.DOTALL)
    assert match, "the walk's exclusion list moved - update this test with it"
    excluded = set(re.findall(r'"([^"]+)"', match.group(1)))

    out: list[Path] = []
    for root, dirs, files in os.walk(BACKEND_ROOT):
        dirs[:] = [d for d in dirs if d not in excluded]
        out.extend(Path(root) / f for f in files if f.endswith(".py"))
    return out


def test_the_walk_reaches_the_modules_the_checks_are_about():
    names = {p.name for p in _scanned_files()}
    for required in ("main.py", "openrouter.py", "completions.py",
                     "secrets_service.py", "network_client.py", "config.py"):
        assert required in names, f"{required} is not scanned by the privacy gate"


def test_the_walk_does_not_scan_itself_or_the_tests():
    """Its own quoted patterns, and fixtures faking upstream errors, are not
    privacy leaks - letting them turn the suite red is how a gate stops being
    read."""
    parts = {part for p in _scanned_files() for part in p.parts}
    assert "verify" not in parts
    assert "tests" not in parts
    assert ".venv" not in parts


def test_the_gate_would_catch_a_real_leak():
    """The whole point: a wildcard CORS origin in main.py must be findable."""
    import os

    pattern = re.compile(r'allow_origins\s*=\s*\["\*"\]|allow_origins.*\*')
    main = BACKEND_ROOT / "main.py"
    assert main in _scanned_files(), "main.py is not reachable by the gate"
    # It is clean today...
    assert not any(pattern.search(line) for line in main.read_text(
        encoding="utf-8").splitlines())
    # ...and the pattern does match the thing it is looking for.
    assert pattern.search('    allow_origins=["*"],')
    assert os.path.exists(main)


def test_prose_does_not_trip_a_check_that_asks_for_it():
    """A sentence explaining a decision is not the decision.

    Without this, documenting WHY the app port is remembered ("localStorage and
    IndexedDB are keyed by scheme://host:port") turned P-16 red - and the only
    way to keep the gate green would be to stop writing the explanation, which
    is a bad trade for a suite whose whole purpose is to be read.
    """
    # The module runs the whole suite on import, so its source is the contract.
    source = (VERIFY_DIR / "verify_elysium_full.py").read_text(encoding="utf-8")
    assert "skip_prose" in source
    assert 'stripped.startswith("#")' in source
    assert "in_doc" in source, "docstrings are not skipped, only comments"


def test_the_gate_still_reads_real_code_lines():
    """Control: skipping prose must not skip everything."""
    source = (VERIFY_DIR / "verify_elysium_full.py").read_text(encoding="utf-8")
    # The skip is opt-in per check, not a blanket default.
    assert "skip_prose: bool = False" in source
    assert "skip_comments: bool = False" in source
