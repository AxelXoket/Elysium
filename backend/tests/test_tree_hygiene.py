"""The hygiene rules, enforced over THIS tree, from inside the suite.

Separate from test_hygiene_gate.py on purpose. That file opens by promising
that nothing in it touches the repository, git, or the real allowlist, so that
its 83 tests keep passing on a machine whose working tree happens to be dirty.
That promise is worth keeping and it is the exact opposite of what this file
does, which is to go red precisely when the tree is dirty.

WHY THE HOOK IS NOT ENOUGH, WHICH IS THE WHOLE REASON THIS EXISTS

`backend/verify/hooks/pre-commit` runs the same sweep and runs it faster, on
the staged content, at the moment it is cheapest to fix. It is still the wrong
thing to rely on:

  - It is not cloned. `.git/hooks` lives in no repository. Somebody who clones
    this today has no hook, gets no warning, and their first commit is
    unchecked. The install is a manual step and a manual step is a step that
    gets skipped.
  - It is skippable four legitimate ways. `--no-verify`, `merge`, `rebase` and
    `cherry-pick` all bypass it (measured, not assumed), and none of them say
    that a check did not run.
  - `core.hooksPath` can point somewhere else entirely.

Every one of those failures is silent. A gate whose absence looks identical to
its success is the shape that left test_release_tree.py dark for months, and
the answer here is the same as it was there: put it in the suite everybody
runs, and put a measured floor under it.

WHY THIS IS NOT A BANNED SOURCE SCAN

The house rule bans a test that reads source text AS A SUBSTITUTE for driving
behaviour. This is the exception the rule names: the text IS the subject. There
is no behaviour behind "no em dash appears in this repository" to observe
instead, in the same way there is none behind test_release_tree.py's question
about what git actually publishes.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

# Loaded by path rather than imported, because backend/verify is not a package.
# The sharper reason has expired: importing it used to make
# `verify_elysium_full` importable as well, and importing THAT ran an entire
# regression suite as an import side effect. That file was deleted on
# 2026-08-17. Loading by path is still right, and now it is only tidiness.
_GATE_PATH = Path(__file__).resolve().parent.parent / "verify" / "verify_hygiene.py"
_spec = importlib.util.spec_from_file_location("verify_hygiene", _GATE_PATH)
assert _spec and _spec.loader
hygiene = importlib.util.module_from_spec(_spec)
# Registered BEFORE exec_module, not after. @dataclass resolves annotations by
# looking its own class's __module__ up in sys.modules, so a module that runs
# before it is registered gets None there and dies on the first decorated
# class. The name has to match the one given to spec_from_file_location.
sys.modules["verify_hygiene"] = hygiene
_spec.loader.exec_module(hygiene)

#: Measured 2026-08-10: the sweep reads 509 files in 0.18 s.
#:
#: A floor, not a target, and the reason this file is not decoration. Every
#: assertion below has the shape "the list of problems is empty", and an empty
#: list is exactly what a sweep that read NOTHING produces. A REPO_ROOT that
#: resolved somewhere unexpected, a .gitignore that grew a line, a git that is
#: not on PATH: all three turn this file green while proving nothing.
#:
#: 400 leaves room for a hundred files to go before anyone has to think about
#: this number. If it fires, re-measure. Do not nudge it down.
_SWEEP_FLOOR = 400


@pytest.fixture(scope="module")
def sweep():
    """The real tree, read once for the whole file.

    Module scope because the sweep costs 0.18 s and seven tests ask about it.
    """
    problems: list[str] = []
    files = list(hygiene.worktree_files(problems))
    waivers, errors = hygiene.load_allowlist()
    return {
        "files": files,
        "paths": {rel for rel, _ in files},
        "hits": hygiene.scan(files, waivers),
        "waivers": waivers,
        "errors": errors,
        "problems": problems,
    }


def test_git_is_how_this_gate_reads_the_tree():
    """The watchman's watchman, and it fails rather than skips.

    `worktree_files` shells out to `git ls-files`. Without git it answers
    nothing, and every emptiness assertion below becomes trivially true. A
    skipped gate and a passing gate are the same line in the summary, which is
    the entire problem this file was written to solve, so this one is asked
    first and answered loudly.
    """
    proc = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                          cwd=hygiene.REPO_ROOT, capture_output=True)
    assert proc.returncode == 0 and proc.stdout.strip() == b"true", (
        f"git cannot read {hygiene.REPO_ROOT} as a work tree, so the sweep in "
        f"this file read nothing and proves nothing. Run the suite from a "
        f"clone rather than an extracted archive, or install git."
    )


def test_the_sweep_reads_the_whole_repository(sweep):
    """Enough files, and files from every tree that has rules over it.

    Named directories rather than named files: renaming a file should not
    break this, and losing a whole subtree should.
    """
    count = len(sweep["files"])
    assert count >= _SWEEP_FLOOR, (
        f"the sweep read {count} files and the floor is {_SWEEP_FLOOR}. Either "
        f"REPO_ROOT resolved somewhere unexpected (it is {hygiene.REPO_ROOT}), "
        f"or .gitignore now excludes something it should not. Every other "
        f"assertion in this file is vacuous until this one is right."
    )
    for tree in ("backend/", "frontend/src/", "docs/"):
        assert any(p.startswith(tree) for p in sweep["paths"]), (
            f"nothing under {tree} reached the sweep, so no rule is protecting it"
        )


def test_no_rule_has_quietly_stopped_applying_to_anything(sweep):
    """A rule whose scope matches no file is switched off, and says nothing.

    Two of the five are scoped to `frontend/src/**.ts(x)`. A moved or renamed
    frontend would leave them enforcing nothing while the report still prints
    five rules and still says PASS.
    """
    dead = [rule.rid for rule in hygiene.RULES
            if not any(rule.scope(p) for p in sweep["paths"])]
    assert not dead, (
        f"these rules matched no file in the tree and are therefore enforcing "
        f"nothing: {dead}. Their scope predicate in verify_hygiene.py no "
        f"longer describes where the code actually lives."
    )


def test_the_tree_breaks_no_rule(sweep):
    """The gate.

    The message is written for somebody who has never opened this file and has
    just had a green suite turn red: what was found, where, why that rule
    exists at all, and the two ways forward.
    """
    hits = sweep["hits"]
    if not hits:
        return
    lines = ["", f"{len(hits)} hygiene violation(s) in the working tree:", ""]
    for hit in hits[:20]:
        lines.append(f"  {hit.path}:{hit.lineno}  [{hit.rule.rid}] {hit.rule.what}")
        lines.append(f"      {hit.text[:96]}")
    if len(hits) > 20:
        lines.append(f"  ... and {len(hits) - 20} more")
    lines += [
        "",
        hits[0].rule.why,
        "",
        "Two ways forward.",
        "",
        "  1. Fix the line. This is almost always the answer, and it usually",
        "     means typing a plain hyphen.",
        "  2. If the line cannot change - a test that asserts on a forbidden",
        "     pattern has to contain that pattern - add a waiver to",
        "     backend/verify/hygiene_allowlist.txt with a written reason.",
        "",
        "Run `python backend/verify/verify_hygiene.py` for a pasteable waiver",
        "record for each hit above.",
    ]
    raise AssertionError("\n".join(lines))


def test_every_file_in_the_tree_is_readable_as_utf8(sweep):
    """A file this gate cannot decode is a file no rule can inspect.

    Reported, never skipped. The first version of `decode` used
    errors="replace", so a cp1252 em dash (byte 0x97) was destroyed before any
    pattern ran and the file came back unreadable AND clean.
    """
    assert not sweep["problems"], "\n".join(
        ["", "these files are not valid UTF-8:", ""]
        + [f"  {p}" for p in sweep["problems"]])


def test_the_waiver_list_has_nothing_dead_in_it(sweep):
    """An exemption that outlived the thing it excused.

    This is the only run entitled to judge every waiver, because it is the only
    one that reads every file. A commit-time sweep sees a handful of paths and
    cannot tell a waiver that is wrong from one whose file it simply did not
    open. Without this, a list of exceptions only ever grows.
    """
    dead = hygiene.stale_waivers(sweep["waivers"], sweep["paths"], full=True)
    assert not dead, "\n".join(
        ["", "these waivers matched nothing in the tree:", ""]
        + [f"  {w.rid} {w.path}\n      line: {w.line[:96]}" for w in dead]
        + ["",
           "The line was edited, moved, or deleted. Remove the waiver, or",
           "re-anchor it to the line as it reads now."])


def test_the_waiver_list_itself_parses(sweep):
    """A malformed allowlist must not silently waive nothing, or everything."""
    assert not sweep["errors"], "\n".join(
        ["", "backend/verify/hygiene_allowlist.txt has problems:", ""]
        + [f"  {e}" for e in sweep["errors"]])


#: The three tools that survived 2026-08-17, each because it does something no
#: test can: the hygiene gate the commit hook runs, the one that makes a live
#: request with a real key, and the one that measures real hardware.
LIVE_VERIFY_TOOLS = {
    "verify_hygiene.py",
    "verify_image_output.py",
    "verify_tts_latency.py",
    # Added 2026-08-30 with the two enforcement lanes. It is not a retired
    # script coming back: nothing by this name was ever deleted, and it is
    # the entry point both installed git hooks run. Its own module docstring
    # argues why the work is split into a cheap lane and a slow one.
    "run_gates.py",
}


def test_the_retired_verify_scripts_stay_retired():
    """The absence scan the house rule allows: it pins a deletion.

    Twelve scripts and a shared harness were deleted, and three defect records
    closed by their subject ceasing to exist rather than by being repaired.
    That only holds while they stay gone. Two of them could not even be
    imported, and nobody noticed for months precisely because nothing looked.

    Compared as a SET, in both directions. A test that only checked the old
    names were absent would say nothing if the three live tools disappeared
    too, and a directory with nothing in it is not the state this describes.
    """
    verify_dir = Path(__file__).resolve().parent.parent / "verify"
    present = {p.name for p in verify_dir.glob("*.py")}
    assert present == LIVE_VERIFY_TOOLS, (
        f"backend/verify/ holds {sorted(present)}. It is meant to hold exactly "
        f"{sorted(LIVE_VERIFY_TOOLS)}. If a retired script came back, "
        f"docs/VERIFY_SCRIPTS_RETIRED.md says why it went; if a live tool is "
        f"gone, that is a check nothing else performs."
    )
    assert not (verify_dir / "_harness.py").exists(), (
        "the harness is back. It left 136 test vaults in %TEMP% over twelve "
        "days, and the tests that reaped them went with it."
    )


def test_every_name_crypto_writes_beside_the_vault_is_ignored():
    """The vault's identity files, and the backups crypto.py makes of them,
    must never be committable.

    `salt.bin` + `verifier.bin` together are an offline, unlimited-rate oracle
    against the passphrase. That is why the bare names are ignored - but until
    2026-08-20 the BACKUPS were not. `.gitignore` carried `*.bak`, and its own
    comment claimed that covered "salt.bin.new, verifier.bin.bak"; crypto.py
    has never written a bare `.bak`. It writes `<name>.bak-<unix ts>` in three
    places, and `*.bak` wants a name that ENDS in `.bak`. In a dev checkout
    DATA_DIR is `backend/`, so a passphrase change dropped both files straight
    into the work tree as untracked files `git add -A` would have staged, in a
    repository that is public.

    The names here are written to the SHAPE crypto.py builds - stem plus
    `.bak-<ts>` - rather than imported, because crypto.py has no constant to
    import: it composes them inline in initialize, in change_passphrase, and
    in the shelve path that follows it. Say that plainly
    rather than claim more: if somebody renames the stems, this test goes on
    passing while the new names sit unignored. The stems are also asserted
    bare, so at least the live files stay covered. Asked of git itself, not of the .gitignore text: a rule can be
    present and still be shadowed by a later negation.
    """
    ts = 1755690000
    names = []
    for stem in ("salt.bin", "verifier.bin", "kdf.json",
                 "vault.recovery"):
        names.append(f"backend/{stem}")
        names.append(f"backend/{stem}.bak-{ts}")   # the three shelve sites
        names.append(f"backend/{stem}.new")        # the crash-safe temporary
    # GROUND: the check can report "not ignored", or it proves nothing.
    control = "backend/main.py"
    proc = subprocess.run(
        ["git", "check-ignore", "--stdin", "-v"],
        cwd=hygiene.REPO_ROOT, capture_output=True,
        input="\n".join(names + [control]).encode(),
    )
    ignored = {
        line.split("\t")[-1].replace("\\", "/")
        for line in proc.stdout.decode().splitlines() if "\t" in line
    }
    assert control not in ignored, (
        "the control file is ignored too, so this test cannot tell an ignored "
        "path from an unignored one and its result means nothing"
    )
    missing = [n for n in names if n not in ignored]
    assert not missing, (
        f"these vault identity artefacts are NOT ignored and could be "
        f"committed to a public repository: {missing}"
    )
