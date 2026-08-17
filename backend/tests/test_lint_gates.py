"""test_lint_gates.py - the lint config is only a gate if something runs it.

KADEME 21, rewritten in K-34. `backend/ruff.toml` narrows ruff to the engine
worker directory. A config file on its own is the failure this project already
measured once: sixteen scripts under backend/verify/, nobody running them,
three dead. So the config gets a caller, and the caller lives in the suite
everybody runs.

WHY ONLY THE WORKER DIRECTORY, written down because the narrowness looks like
an oversight and is not. `tts/adapters/` holds the HOST half of each engine and
our own interpreter imports it (`from ..base import TtsAdapter`), so an
undefined name there fails at import time in this very suite. `tts/worker/`
holds the ENGINE half: it is shipped as DATA (elysium_onefile.spec:35-39) and
run by a foreign venv we do not control, so nothing here ever parses it. Ruff's
scope is exactly the set of Python files this suite cannot see, and the test
below ties it to that fact rather than to a pattern somebody remembered.

WHAT K-34 FOUND, all four measured on ruff 0.16.3:

  * `select = []` left every test in this file GREEN with two real F
    violations on disk, because the rule list lived here as a constant instead
    of being read back from the config.
  * `include` narrowed to a single file left the scope test GREEN, because it
    asked `assert files` - non-empty - rather than comparing the scope to the
    set it has to cover.
  * The three positive controls all ran `--isolated --select F`, which asks
    "can ruff do this" and answers yes whatever our config says. The question a
    gate has to ask is "does the config WE SHIP catch this".
  * And a hole the ledger did not have: `exclude` under `[lint]` rather than at
    the top level keeps a file in `--show-files` - so it stays inside a scope
    comparison - while quietly skipping it. Only a control running the shipped
    config catches that, which is why the one below does not use `--isolated`.

WHAT THIS GATE CANNOT DO:

  * The syntax claim is config-independent; the F-rule claims are not. Measured:
    a parse failure is reported with `select = []`, an undefined name is not.
    Only the syntax control below may keep `--isolated`, and it says so.
  * Ruff runs in whichever interpreter runs pytest. This suite needs
    `backend/.venv`; the system interpreter here carries a different ruff, and
    on 0.15.10 the same directory reports "All checks passed" where 0.16.3
    reports forty findings. The skip below is loud, and ruff is declared in
    requirements-dev.txt so a fresh checkout installs it instead of silently
    skipping the gate.
"""

from __future__ import annotations

import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
WORKER = BACKEND / "tts" / "worker"

#: The two rules the controls below actually exercise. NOT a copy of
#: `ruff.toml`'s selection - it is the minimum this gate depends on, and it is
#: checked against what ruff RESOLVED rather than against what the file says.
#: A narrowed selection makes these disappear from the resolved set, which is
#: the symmetry the old `SELECT = ["F"]` constant destroyed.
REQUIRED_RULES = ("F401", "F821")


def _ruff(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--no-cache", *args],
        cwd=BACKEND, capture_output=True, text=True, timeout=180,
        input=stdin,
    )


def _have_ruff() -> bool:
    try:
        return subprocess.run([sys.executable, "-m", "ruff", "--version"],
                              capture_output=True, timeout=60).returncode == 0
    except Exception:
        return False


needs_ruff = pytest.mark.skipif(
    not _have_ruff(),
    reason="ruff is not installed in this interpreter - it is in "
           "requirements-dev.txt: .venv/Scripts/python.exe -m pip install "
           "-r requirements-dev.txt",
)


def resolved_settings() -> tuple[list[str], set[str], list[str]]:
    """What ruff ACTUALLY resolved for a worker file.

    Returns `(file_resolver.include, enabled rule codes, linter.exclude)`.

    Read from `--show-settings` rather than by parsing ruff.toml. Stronger, and
    the difference is not cosmetic: a selection written under the wrong table
    lands in the file but not in the resolved settings, and a TOML reader would
    report the config's intention where this reports its effect.
    """
    done = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--show-settings",
         str(WORKER / "_dsp.py")],
        cwd=BACKEND, capture_output=True, text=True, timeout=180,
    )
    assert done.returncode == 0, done.stdout or done.stderr
    out = done.stdout

    def _list(key: str) -> list[str]:
        block = re.search(re.escape(key) + r" = \[(.*?)\]", out, re.S)
        assert block, "ruff did not report %s:\n%s" % (key, out[:800])
        return re.findall(r'"([^"]+)"', block.group(1))

    include = _list("file_resolver.include")
    linter_exclude = _list("linter.exclude")
    # Rule codes appear as `undefined-name (F821),` in the enabled list.
    codes = set(re.findall(r"\(([A-Z]+[0-9]+)\)", out))
    return include, codes, linter_exclude


def shipped_worker_files() -> set[Path]:
    """The worker halves the spec sends out as data files.

    Derived by the spec's own rule (elysium_onefile.spec:35-39: every `*.py`
    under tts/worker except the package marker), so a seventh engine is covered
    the day it is added. This is the set that motivates the gate: these are the
    files a foreign interpreter runs and nothing in this suite imports.
    """
    return {p for p in WORKER.glob("*.py") if p.name != "__init__.py"}


@needs_ruff
def test_every_worker_file_is_clean():
    """Syntax errors and undefined names in a worker do not surface until an
    engine venv we do not control tries to run the file - on a user's
    machine, as 'the environment is broken'.

    Every file is named EXPLICITLY rather than handing ruff the directory.
    Measured while closing K-34, and the reason is the whole `linter.exclude`
    hole: with `exclude = ["tts/worker/**"]` under `[lint]`, a real undefined
    name planted in `_dsp.py` made `ruff check .` say "All checks passed"
    while `ruff check tts/worker/_dsp.py` reported it - because ruff lets an
    explicitly named path through its exclusions and a directory walk obeys
    them. Passing the directory therefore meant a single config line could
    silence this test on real violations. Explicit paths cannot be silenced
    that way, and the scope test below is what proves the list of paths is
    still the whole set.
    """
    files = sorted(WORKER.glob("*.py"))
    assert files, "no worker files were found, so this test checked nothing"
    done = _ruff(*(str(p) for p in files))
    assert done.returncode == 0, done.stdout or done.stderr


@needs_ruff
def test_nothing_is_excluded_from_linting_behind_the_scope_check():
    """The hole the scope check cannot see, and it is not in the ledger.

    `exclude` under `[lint]` is invisible to everything else this file does.
    Measured: with `exclude = ["tts/worker/**"]` there, `--show-files` still
    lists all seven worker files - so the scope comparison passes - and
    `--stdin-filename` still reports violations, so the positive controls pass
    too. Every gate in the old version of this file, and every gate in the new
    one except this test, stayed green while two real F violations sat in
    `_dsp.py` unreported by a directory run.

    So the resolved exclusion is read directly. An entry here is not
    automatically wrong, but it can hide a worker half from the linter without
    changing anything else that is observable, and that deserves to be argued
    for in a diff rather than discovered later.
    """
    _, _, linter_exclude = resolved_settings()
    assert linter_exclude == [], (
        "ruff resolved a lint-level exclusion: %s\n"
        "Files matched by it are still listed by --show-files and still checked "
        "when named explicitly, so the scope test and the positive controls "
        "cannot see this. If the entry is deliberate, say why here and narrow "
        "this assertion; do not delete it." % linter_exclude)


@needs_ruff
def test_the_shipped_selection_still_contains_the_rules_this_gate_relies_on():
    """Read the config back out of ruff, do not restate it here.

    K-34's first measurement: with `select = []` in ruff.toml, every test in
    this file stayed green while two real F violations sat on disk. The old
    `SELECT = ["F"]` constant was what made that possible - it kept the
    controls firing on a selection the config no longer shipped.
    """
    _, codes, _ = resolved_settings()

    # GROUND: a resolved set of almost nothing would satisfy nothing below, but
    # it would also mean the parse above failed rather than the config being
    # empty, and those two deserve different messages.
    assert len(codes) > 10, (
        "ruff reported almost no enabled rules (%d) - either the config selects "
        "nothing or --show-settings changed shape" % len(codes))

    for code in REQUIRED_RULES:
        assert code in codes, (
            "%s is not in the selection ruff resolved, so the control that "
            "relies on it is measuring a rule we no longer ship" % code)


@needs_ruff
def test_the_scope_covers_every_file_a_foreign_interpreter_runs():
    """The `include` line is the whole safety of this gate, and 'non-empty' is
    not a measurement of it.

    K-34's second measurement: narrowing `include` to one file left the old
    version of this test green while 87% of the scope disappeared, because it
    asserted `assert files`. The comparison here is against the set that has to
    be covered - and that set is derived from the packaging fact that makes
    these files unverifiable any other way, not from a remembered pattern.

    Measured in KADEME 21 and still true: `ruff check backend --select E9,F`
    reports 119 findings, so a config that quietly WIDENED would either turn
    the suite red for a backlog nobody agreed to, or get its selection relaxed
    to make it pass - leaving the worker unguarded either way. Hence equality,
    which fails in both directions.
    """
    done = _ruff(".")
    assert done.returncode == 0, done.stdout or done.stderr

    listed = _ruff(".", "--show-files")
    scanned = {
        Path(line.strip()).resolve()
        for line in listed.stdout.splitlines() if line.strip()
    }
    on_disk = {p.resolve() for p in WORKER.glob("*.py")}

    assert scanned == on_disk, (
        "ruff's scope and the worker directory disagree.\n"
        "  scanned but not on disk: %s\n"
        "  on disk but not scanned: %s"
        % (sorted(p.name for p in scanned - on_disk),
           sorted(p.name for p in on_disk - scanned))
    )

    # And the reason the scope is what it is: every file the spec hands to a
    # foreign interpreter must be inside it. Checked separately from the
    # equality above so a failure says WHICH property broke.
    shipped = {p.resolve() for p in shipped_worker_files()}
    assert shipped <= scanned, (
        "these worker halves are shipped as data and run by an engine venv, "
        "but ruff does not check them: %s"
        % sorted(p.name for p in shipped - scanned))
    assert shipped, "no worker halves were derived - the comparison is vacuous"


@needs_ruff
def test_the_shipped_config_catches_an_undefined_name():
    """POSITIVE CONTROL, and the only test here that proves the gate works.

    No `--isolated`, no `--select`: the synthetic file is fed through
    `--stdin-filename` at a path inside the worker directory, so ruff resolves
    the SAME configuration a real worker gets. K-34's third measurement is why
    this replaced three `--isolated --select F` controls, which answered "can
    ruff find an undefined name" - a question whose answer is yes regardless of
    what we ship.

    It is also the only thing that catches `exclude` written under `[lint]`:
    such a file stays in `--show-files`, so the scope comparison above passes
    it, and is silently never linted.
    """
    done = _ruff("--stdin-filename", "tts/worker/_probe.py", "-",
                 stdin="def synth(text):\n    return numpy.zeros(3)\n")
    assert done.returncode != 0, (
        "the config we ship reported an undefined name as clean:\n%s"
        % (done.stdout or done.stderr))
    assert "F821" in done.stdout, done.stdout


@needs_ruff
def test_the_shipped_config_catches_an_unused_import():
    """The second rule REQUIRED_RULES names, through the same shipped path.

    Two rules rather than one on purpose: a selection narrowed to exactly one
    of them would leave a single-rule control green, and `select = ["F401"]`
    is a far more plausible accident than `select = []`.
    """
    done = _ruff("--stdin-filename", "tts/worker/_probe.py", "-",
                 stdin="import os\n\n\ndef synth(text):\n    return len(text)\n")
    assert done.returncode != 0, (
        "the config we ship reported an unused import as clean:\n%s"
        % (done.stdout or done.stderr))
    assert "F401" in done.stdout, done.stdout


@needs_ruff
def test_clean_code_is_not_flagged_by_the_shipped_config():
    """And the controls are discriminating, not just always-red.

    A configuration that rejected every input would pass both controls above
    and would not be a lint gate, it would be an outage.
    """
    done = _ruff("--stdin-filename", "tts/worker/_probe.py", "-",
                 stdin=textwrap.dedent("""\
                     import math


                     def synth(text):
                         return math.floor(len(text))
                     """))
    assert done.returncode == 0, done.stdout


@needs_ruff
def test_only_the_parse_failure_survives_an_empty_selection(tmp_path):
    """The claim that is deliberately config-independent, and its LIMIT.

    The old docstring said the syntax control proved coverage "survives ANY
    future narrowing of `select`". Half true, and the half that is false is what
    let K-34 hide: measured here against a synthetic `select = []`, a parse
    failure is still reported and an undefined name is NOT. So parse coverage is
    free and F coverage is bought with the selection - which is why the two
    controls above had to move onto the shipped config, and why E9 could be
    dropped from ruff.toml without losing anything.

    Both directions are asserted. Asserting only the first would leave this test
    agreeing with a claim it does not check, which is the shape of the defect it
    documents.
    """
    empty = tmp_path / "ruff.toml"
    empty.write_text("[lint]\nselect = []\n", encoding="utf-8")

    bad = tmp_path / "broken.py"
    bad.write_text("def f(:\n    pass\n", encoding="utf-8")
    parse = _ruff("--config", str(empty), str(bad))
    assert parse.returncode != 0, (
        "a file that cannot be parsed was reported clean even though parse "
        "failures are supposed to ignore the selection")
    assert "invalid-syntax" in parse.stdout, parse.stdout

    undefined = tmp_path / "undefined.py"
    undefined.write_text("def g(t):\n    return numpy.zeros(3)\n", encoding="utf-8")
    rules = _ruff("--config", str(empty), str(undefined))
    assert rules.returncode == 0, (
        "an undefined name was reported under `select = []`, so F coverage does "
        "NOT depend on the selection after all - the two controls above could "
        "then be simplified, and this file's reasoning needs rewriting:\n%s"
        % rules.stdout)


@needs_ruff
def test_the_adapters_are_covered_by_this_suite_instead(tmp_path):
    """The stated reason ruff stops at the worker directory, made testable.

    `tts/adapters/` is not linted because our own interpreter imports it, so an
    undefined name there is an ImportError in this suite. That sentence is only
    true while the package really is importable from here - if adapters ever
    became data files run elsewhere, the lint scope would have to grow and
    nothing would say so. This is that alarm.
    """
    import importlib

    mod = importlib.import_module("tts.adapters")
    assert getattr(mod, "ADAPTERS", ()), (
        "tts.adapters imports but declares no adapters, so 'this suite would "
        "notice a broken adapter' no longer holds")
