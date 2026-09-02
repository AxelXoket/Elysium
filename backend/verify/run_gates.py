"""The gates that actually run, and the two moments they run at.

Until now the repository had one installed hook and it ran verify_hygiene.py
and nothing else. No CI, no pytest, no vitest, no tsc, no eslint: 3193 backend
tests and 1836 frontend ones, every one of them invoked by hand. A gate that
nobody runs reports the same thing as a gate that passes, which is the exact
failure the contract registry exists to refuse, one level up.

Two lanes, because one budget cannot hold both jobs.

  pre-commit   Hygiene over the index, the installed hooks, and every test
               file the commit itself is staging. A commit that stages a red
               test is refused at the moment it is made.

               What it COSTS, measured rather than promised. A commit that
               stages no test file: 0.2s. One ordinary test file: about 2s,
               most of it pytest starting up. One of the two contract files:
               10s and 20s, because those now run a hundred and fifty other
               tests in a child process. The commit that introduced this lane
               staged five test files and paid 38s.

               So the under-two-seconds rule for a gate test holds for the
               common case and does not hold for a commit that stages a
               slow file. Written down rather than rounded off: the honest
               shape is "you pay for what you changed", and on this tree one
               of the things you can change is expensive.

  pre-push     Everything, including the build. The full backend suite, the
               frontend typecheck, the linter, the full vitest run, and then a
               real PyInstaller build of Elysium.exe followed by its selftest.
               Slow, and it is meant to be: this is the last moment before the
               work leaves this machine, and "it compiled" is a claim nobody
               was checking either.

THE BUILD LANE VERIFIES, IT DOES NOT PRODUCE. Settled 30 August 2026, and it
is a boundary rather than a preference:

    Pre-push verification must not mutate, replace, regenerate, touch or
    otherwise alter any tracked release artifact or canonical build output.
    All verification builds are produced outside the repository and validated
    in place. Artifact freshness stays mandatory for canonical release builds
    and is enforced by the release gate, not satisfied as a side effect of
    pre-push verification.

So the frontend bundle is built into a temporary directory outside the tree,
the exe is built with --distpath and --workpath pointing outside it too, the
selftest starts THAT exe, and the whole area is deleted afterwards. Neither
the tracked Elysium.exe nor frontend/dist nor backend/dist is touched.

The first draft did the obvious thing instead, and the obvious thing was a
trap worth writing down: it ran `npm run build`, which rewrote frontend/dist,
which is one of the inputs the artifact gate measures the committed exe
against. Every push made the next push's freshness check fail. A gate that
manufactures its own red is a gate people learn to skip.

Freshness is therefore NOT this lane's question, and the one test that asks it
is deselected here by name, with the reason printed. It still runs in an
ordinary suite run, which is where a release is judged.

No environment variable switches a lane off, and that is measured rather than
asserted. What DOES switch them off, and cannot be fixed from inside a hook:
`git commit --no-verify`, `git push --no-verify`, `core.hooksPath` pointed
somewhere empty, a merge or a rebase (git runs no pre-commit hook for either),
and a fresh clone, because .git/hooks is not cloned. The working-tree hygiene
sweep catches the last three afterwards; nothing catches the first two, which
is why the rule against them is a rule rather than a mechanism.

The lane checks the installed hooks at commit time for exactly this reason: a
deleted .git/hooks/pre-push used to switch off the whole build lane in silence.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
REPO_ROOT = BACKEND.parent
FRONTEND = REPO_ROOT / "frontend"

sys.path.insert(0, str(HERE))
import verify_hygiene  # noqa: E402

#: The interpreter this file is running under. The hook already chose it, and
#: the system python on this machine has no sqlcipher3, so re-deriving it here
#: would pick a different one than the one that works.
PYTHON = sys.executable

#: The line the packaged build prints when it is asked to check itself.
SELFTEST_MARKER = "SELFTEST "

#: The one flag on that line that is not a check. Everything else on it is a
#: boolean the build is reporting about itself, and every one of them has to
#: be True.
#:
#: Deliberately NOT a hardcoded list of the three checks. That list was typed
#: out in three separate files with nothing importing anything, so renaming a
#: check in run_app.py would have left this gate refusing every build forever
#: with no test to say why. The exe decides what it reports; the gate demands
#: that all of it passed.
SELFTEST_NOT_A_FLAG = "status"


def _resolve(program: str) -> str:
    """The real path of a program, so nothing ever needs a shell.

    npm and npx are batch files on Windows and CreateProcess will not start a
    .cmd directly, which is why the first draft reached for `shell=True`. That
    was a hole, not a convenience: with a shell in the way the argument list
    stops being a list. `shutil.which` finds the same file the shell would
    have found and hands it over as one argument.
    """
    found = shutil.which(program)
    if found is None:
        raise RuntimeError(
            f"{program} is not on PATH, so this gate cannot run. Refusing "
            "rather than reporting a step that never happened."
        )
    return found


def _run(label: str, argv: list[str], cwd: Path,
         env: dict[str, str] | None = None) -> bool:
    """One step. Streams its output, reports how long it took.

    Never through a shell. The first draft ran anything not ending in .exe
    under `cmd /c`, and a staged file called `a&mkdir,PWNED&rem,y.test.ts`
    then did two things at once: it created a directory during somebody's
    commit, and it made the lane print "ok" for a vitest run that had failed,
    because cmd reports the exit code of the LAST command in the chain. An
    ampersand is a legal filename character on Windows and the argument
    quoting only quotes spaces. Measured, on a real commit.
    """
    print(f"\n  ---- {label} ----", flush=True)
    started = time.monotonic()
    completed = subprocess.run(
        [_resolve(argv[0]), *argv[1:]], cwd=str(cwd), env=env,
        shell=False, check=False)
    elapsed = time.monotonic() - started
    verdict = "ok" if completed.returncode == 0 else "FAILED"
    print(f"  ---- {label}: {verdict} in {elapsed:.1f}s ----", flush=True)
    return completed.returncode == 0


def _staged() -> list[str]:
    """Paths this commit is about to publish, as git spells them."""
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=False)
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def _backend_tests(paths: list[str], repo_root: Path = REPO_ROOT) -> list[str]:
    return sorted(
        p[len("backend/"):] for p in paths
        if p.startswith("backend/tests/")
        and Path(p).name.startswith("test_") and p.endswith(".py")
        and (repo_root / p).is_file()
    )


def _frontend_tests(paths: list[str], repo_root: Path = REPO_ROOT) -> list[str]:
    return sorted(
        p[len("frontend/"):] for p in paths
        if p.startswith("frontend/src/")
        and (p.endswith(".test.ts") or p.endswith(".test.tsx"))
        and (repo_root / p).is_file()
    )


def run_staged_tests(
    paths: list[str],
    repo_root: Path = REPO_ROOT,
    backend: Path = BACKEND,
    frontend: Path = FRONTEND,
) -> bool:
    """Run the test files in `paths`. True when every one of them passed.

    The roots are parameters rather than module constants so this can be
    measured: a lane that only ever runs against the real repository can be
    read but not tested, and "the gate refuses a red test" is the one claim
    this file exists to make.
    """
    backend_tests = _backend_tests(paths, repo_root)
    frontend_tests = _frontend_tests(paths, repo_root)
    ok = True

    if backend_tests:
        ok = _run("pytest (staged test files)",
                  [PYTHON, "-m", "pytest", "-q", "--no-header", *backend_tests],
                  backend) and ok
    if frontend_tests:
        ok = _run("vitest (staged test files)",
                  ["npx", "vitest", "--run", *frontend_tests],
                  frontend) and ok
    if not backend_tests and not frontend_tests:
        print("\n  no test file is staged, so only hygiene ran here.")
        print("  the full suites run on push.")
    return ok


def installed_hooks_are_current() -> bool:
    """Is every hook git will actually run the versioned one?

    verify_hygiene skips this in --staged mode, and its reason is sound for
    the hook it is running inside: asking whether pre-commit is installed
    answers itself. It is not sound for the OTHER hook. Measured: deleting
    .git/hooks/pre-push switched off the whole build lane and every commit
    afterwards still printed PASS, because nothing at commit time ever looked.
    The same silence covered a pre-commit hook edited to `exit 0`.

    So the lane asks, for both, at the one moment somebody is watching.
    """
    ok = True
    for source, _rel, label in verify_hygiene.HOOKS:
        good, message = verify_hygiene.hook_state(source, None, label)
        mark = "ok" if good else "FAILED"
        print(f"\n  ---- installed {label} hook: {mark} - {message}")
        ok = good and ok
    return ok


def pre_commit() -> int:
    """Hygiene over the index, plus the tests this commit is staging."""
    ok = verify_hygiene.main(["--staged"]) == 0
    ok = installed_hooks_are_current() and ok
    ok = run_staged_tests(_staged()) and ok
    return 0 if ok else 1


#: The one test the verification lane must not run, and why.
#:
#: It compares the COMMITTED exe against the mtimes of what went into it. That
#: is a release question: it is answered by rebuilding and committing the
#: artefact, which is exactly the act this lane is forbidden to perform. Left
#: in, the lane would refuse every push from the moment a source file changed
#: until the next release, which is how a gate teaches people to reach for
#: --no-verify. It still runs in an ordinary suite run.
FRESHNESS_TEST = ("tests/test_artifact_gate.py"
                  "::test_the_build_is_not_older_than_what_went_into_it")


def pre_push() -> int:
    """Everything, and then a verification build made outside the tree."""
    ok = verify_hygiene.main([]) == 0

    print("\n  not run here, by the settled rule on artifact freshness:\n"
          f"    {FRESHNESS_TEST}\n"
          "    freshness belongs to the release gate. This lane verifies; it\n"
          "    is not allowed to produce the artefact that would satisfy it.")
    ok = _run("pytest (whole backend suite)",
              [PYTHON, "-m", "pytest", "-q", "--no-header", "tests",
               "--deselect", FRESHNESS_TEST],
              BACKEND) and ok
    ok = _run("tsc (typecheck)", ["npm", "run", "typecheck"], FRONTEND) and ok
    ok = _run("eslint", ["npm", "run", "lint"], FRONTEND) and ok
    ok = _run("vitest (whole frontend suite)",
              ["npx", "vitest", "--run"], FRONTEND) and ok

    return 0 if verification_build(ok) else 1


def verification_build(ok: bool) -> bool:
    """Build the frontend and the exe outside the repository, and boot it.

    Every output lands under a temporary directory that is deleted when this
    returns. frontend/dist, backend/dist, backend/build and the tracked
    Elysium.exe are left exactly as they were: this lane proves the build
    works, and producing the artefact is a separate, deliberate act.

    What it therefore does NOT prove: that the bundle inside the exe is
    current. The exe is packaged from whatever frontend/dist holds right now,
    because regenerating it is the mutation this lane is forbidden to make.
    That is the freshness question, and it belongs to the release gate.
    """
    with tempfile.TemporaryDirectory(prefix="elysium-verify-") as area:
        outside = Path(area)
        ok = _run("vite build (verification copy, outside the repository)",
                  ["npx", "vite", "build",
                   "--outDir", str(outside / "web"), "--emptyOutDir"],
                  FRONTEND) and ok

        started = time.time()
        built = _run("pyinstaller (verification copy, outside the repository)",
                     [PYTHON, "-m", "PyInstaller", "elysium_onefile.spec",
                      "--noconfirm",
                      "--distpath", str(outside / "dist"),
                      "--workpath", str(outside / "work")],
                     BACKEND, env=_build_environment())
        ok = built and ok
        if built:
            ok = _selftest(outside / "dist" / "Elysium.exe", started) and ok
        else:
            # Measured the hard way: the first run of this lane had the build
            # refused by the spec's own guard and the selftest then started
            # the PREVIOUS exe, which answered every question correctly and
            # printed "ok". A selftest of yesterday's artefact is worse than
            # no selftest, because it reads like proof.
            print("\n  ---- selftest: SKIPPED, the build produced no exe ----")
    return ok


def _build_environment() -> dict[str, str]:
    """The environment PyInstaller gets, with PATH cut back to what belongs.

    elysium_onefile.spec refuses a build that would ship a binary from
    outside this project, and on this machine it refused: PATH carried a
    LaTeX distribution whose bin directory holds its own copies of the UCRT
    runtime, and PyInstaller resolves DLLs through PATH. The spec's own
    message names the fix, which is to build from a shell whose PATH does not
    contain that directory. Doing it here rather than asking every developer
    to arrange their shell means the lane builds the same bundle on every
    machine, and the spec's guard stays armed rather than being widened.

    Kept: the repository, the virtualenv, and the Windows system directories
    a compiler and linker genuinely need.
    """
    env = dict(os.environ)
    keep = []
    roots = [str(REPO_ROOT).lower(), str(Path(sys.prefix)).lower()]
    system = [os.environ.get("SystemRoot", r"C:\Windows").lower()]
    for entry in env.get("PATH", "").split(os.pathsep):
        low = entry.strip().lower()
        if not low:
            continue
        if any(low.startswith(root) for root in roots + system):
            keep.append(entry)
    env["PATH"] = os.pathsep.join(keep)
    return env


def _selftest(exe: Path, built_after: float) -> bool:
    """Start the packaged exe once and read what it says about itself.

    A build that produces a file is not a build that works. voice_payload is
    the one that has actually been wrong before: it goes False when tts/worker
    or tts/requirements did not make it into the bundle, and nothing else in
    the pipeline notices.

    `built_after` is when the build started. The exe on disk has to be newer
    than that or this is measuring the last one, which is exactly what
    happened the first time this lane ran.
    """
    print("\n  ---- selftest (packaged exe) ----", flush=True)
    if not exe.is_file():
        print(f"  ---- selftest: FAILED, {exe} was not produced ----")
        return False
    if exe.stat().st_mtime < built_after:
        print(f"  ---- selftest: FAILED, {exe} is older than the build that "
              "was supposed to produce it ----")
        return False

    # ELYSIUM_DATA_DIR is redirected at a throwaway folder, and that is not
    # tidiness. run_app.py's own comment says the frozen self check stays
    # runnable while the real app is open BECAUSE it always redirects it, and
    # test_artifact_gate.py does the same thing for the same reason: a boot
    # check that quietly touched the real database would be a worse bug
    # than anything it could find. Without the redirect this lane claims the
    # single-instance lock against the real app, raises its window or
    # opens a modal box, rewrites the port file the window reads its settings
    # from, and hardens the ACL on the real vault folder.
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="elysium-selftest-") as sandbox:
        env = dict(os.environ)
        env["ELYSIUM_SELFTEST"] = "1"
        env["ELYSIUM_DATA_DIR"] = sandbox
        try:
            completed = subprocess.run(
                [str(exe)], cwd=str(BACKEND), env=env, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=600,
                check=False)
        except subprocess.TimeoutExpired:
            print("  ---- selftest: FAILED, the packaged exe did not answer "
                  "within 600s ----")
            return False
    elapsed = time.monotonic() - started
    output = (completed.stdout or "") + (completed.stderr or "")
    print(output.strip())

    flags, problem = read_selftest_flags(output)
    if problem or completed.returncode != 0:
        print(f"  ---- selftest: FAILED in {elapsed:.1f}s ----")
        if problem:
            print(f"  {problem}")
        return False
    print(f"  ---- selftest: ok in {elapsed:.1f}s ----  ({len(flags)} checks)")
    return True


def read_selftest_flags(output: str) -> tuple[dict[str, bool], str]:
    """The flags off the SELFTEST line, and what is wrong with them.

    Scoped to that one line rather than searched for across the whole output.
    A substring match over stdout and stderr together would be satisfied by a
    traceback that happened to quote the words, which is not the same thing as
    a build reporting them.
    """
    line = next((ln for ln in output.splitlines()
                 if ln.strip().startswith(SELFTEST_MARKER)), None)
    if line is None:
        return {}, "the build printed no SELFTEST line at all"

    flags: dict[str, bool] = {}
    for token in line.strip()[len(SELFTEST_MARKER):].split():
        key, sep, value = token.partition("=")
        if not sep or key == SELFTEST_NOT_A_FLAG:
            continue
        if value in ("True", "False"):
            flags[key] = value == "True"
    if not flags:
        return {}, "the SELFTEST line carried no checks to read"
    failed = sorted(key for key, ok in flags.items() if not ok)
    if failed:
        return flags, f"the build reported these as False: {', '.join(failed)}"
    return flags, ""


LANES = {"pre-commit": pre_commit, "pre-push": pre_push}


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "--hook" or argv[1] not in LANES:
        print(f"usage: run_gates.py --hook {{{'|'.join(LANES)}}}",
              file=sys.stderr)
        return 2
    lane = argv[1]
    started = time.monotonic()
    code = LANES[lane]()
    print(f"\n  {lane}: {'PASS' if code == 0 else 'REFUSED'} "
          f"({time.monotonic() - started:.1f}s total)")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
