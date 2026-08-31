"""The two enforcement lanes, and the claim that they refuse anything.

Before this pass the repository had one installed hook and it ran the hygiene
scanner. No CI, no pytest, no vitest, no tsc, no eslint. Three thousand
backend tests and eighteen hundred frontend ones were invoked by hand, which
means every gate in this tree was green exactly as often as somebody
remembered to look.

The tests below are about the gates rather than about the rules they run.
Three questions, and they are the three the execution queue named:

  MUT-CI-01  a red test in the staged set is REFUSED. Ground control beside
             it: the same lane with a green test exits zero, so a lane that
             refused everything would not pass.
  MUT-CI-02  the versioned hook and the installed one are compared, and one
             space between them is a failure. Both hooks, not just the first.
  MUT-CI-03  no interpreter means refuse, not wave through. A guard that
             fails open reports nothing on exactly the machine where
             something is already misconfigured.

Nothing here writes into the repository: the lane takes its roots as
parameters and every case builds its own tree under tmp_path.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "verify"))

import run_gates  # noqa: E402
import verify_hygiene  # noqa: E402

_REPO = Path(__file__).resolve().parents[2]
_HOOKS = _REPO / "backend" / "verify" / "hooks"

#: The real runner, kept so the two tests that swap it out can put it back.
#: Captured at import, before anything has had a chance to replace it.
_REAL_RUN = run_gates._run

RED = "def test_it_fails():\n    assert False\n"
GREEN = "def test_it_passes():\n    assert True\n"


def _backend_tree(root: Path, body: str, name: str = "test_probe.py") -> Path:
    tests = root / "backend" / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    (tests / name).write_text(body, encoding="utf-8")
    return root


class TestTheCommitLaneRefusesARedTest:
    """MUT-CI-01. The whole point, stated as a question the lane can answer."""

    def test_a_staged_test_that_fails_stops_the_commit(
        self, tmp_path: Path,
    ) -> None:
        root = _backend_tree(tmp_path, RED)
        assert run_gates.run_staged_tests(
            ["backend/tests/test_probe.py"],
            repo_root=root, backend=root / "backend",
        ) is False

    def test_a_staged_test_that_passes_does_not(self, tmp_path: Path) -> None:
        # GROUND CONTROL. Without it, a lane that refused every commit would
        # satisfy the case above and nothing would say so.
        root = _backend_tree(tmp_path, GREEN)
        assert run_gates.run_staged_tests(
            ["backend/tests/test_probe.py"],
            repo_root=root, backend=root / "backend",
        ) is True

    def test_one_red_file_beside_a_green_one_still_refuses(
        self, tmp_path: Path,
    ) -> None:
        root = _backend_tree(tmp_path, GREEN, "test_green.py")
        _backend_tree(root, RED, "test_red.py")
        assert run_gates.run_staged_tests(
            ["backend/tests/test_green.py", "backend/tests/test_red.py"],
            repo_root=root, backend=root / "backend",
        ) is False

    def test_a_commit_that_stages_no_test_still_runs(
        self, tmp_path: Path,
    ) -> None:
        # Deliberate, and the reason the lane is cheap: a commit that touches
        # no test file pays for hygiene only. Recording it here so the day
        # somebody widens this, the change is visible rather than assumed.
        root = _backend_tree(tmp_path, GREEN)
        assert run_gates.run_staged_tests(
            ["README.md"], repo_root=root, backend=root / "backend",
        ) is True


class TestWhatTheLaneSelects:
    """Selection is a pure function, so it is measured as one."""

    def test_it_takes_backend_test_files_and_nothing_else(
        self, tmp_path: Path,
    ) -> None:
        root = _backend_tree(tmp_path, GREEN, "test_probe.py")
        (root / "backend" / "tests" / "helper.py").write_text("", "utf-8")
        (root / "backend").joinpath("notebook_store.py").write_text("", "utf-8")

        assert run_gates._backend_tests([
            "backend/tests/test_probe.py",
            "backend/tests/helper.py",
            "backend/notebook_store.py",
            "backend/tests/test_deleted.py",
        ], root) == ["tests/test_probe.py"]

    def test_it_takes_frontend_test_files_and_nothing_else(
        self, tmp_path: Path,
    ) -> None:
        src = tmp_path / "frontend" / "src" / "test"
        src.mkdir(parents=True)
        (src / "a.test.ts").write_text("", "utf-8")
        (src / "b.test.tsx").write_text("", "utf-8")
        (src / "helpers.ts").write_text("", "utf-8")

        assert run_gates._frontend_tests([
            "frontend/src/test/a.test.ts",
            "frontend/src/test/b.test.tsx",
            "frontend/src/test/helpers.ts",
            "frontend/src/test/gone.test.ts",
        ], tmp_path) == ["src/test/a.test.ts", "src/test/b.test.tsx"]


class TestBothHooksAreCompared:
    """MUT-CI-02, for the pre-push hook as well as the pre-commit one.

    The pre-push lane is the expensive one, so it is the one somebody is most
    likely to leave uninstalled, and an uninstalled hook is invisible.
    """

    @pytest.mark.parametrize("label", ["pre-commit", "pre-push"])
    def test_a_one_space_difference_is_reported(
        self, tmp_path: Path, label: str,
    ) -> None:
        source = _HOOKS / label
        installed = tmp_path / label
        installed.write_bytes(
            source.read_bytes().replace(b"set -e", b"set  -e", 1))

        ok, message = verify_hygiene.hook_state(
            str(source), str(installed), label)
        assert ok is False
        assert f"the installed {label} hook differs" in message

    @pytest.mark.parametrize("label", ["pre-commit", "pre-push"])
    def test_an_identical_copy_is_accepted(
        self, tmp_path: Path, label: str,
    ) -> None:
        # GROUND CONTROL: the comparison is not simply refusing everything.
        source = _HOOKS / label
        installed = tmp_path / label
        shutil.copyfile(source, installed)

        ok, message = verify_hygiene.hook_state(
            str(source), str(installed), label)
        assert ok is True
        assert message == f"{label} hook installed and current"

    @pytest.mark.parametrize("label", ["pre-commit", "pre-push"])
    def test_nothing_installed_names_the_hook_that_is_missing(
        self, tmp_path: Path, label: str,
    ) -> None:
        ok, message = verify_hygiene.hook_state(
            str(_HOOKS / label), str(tmp_path / "absent"), label)
        assert ok is False
        assert f"no {label} hook is installed" in message

    def test_git_is_asked_where_each_hook_lives(self) -> None:
        # core.hooksPath moves the whole directory. Asking git rather than
        # assuming .git/hooks is why this works at all, and the second lane
        # has to ask the same question about its own name.
        for label in ("pre-commit", "pre-push"):
            assert verify_hygiene.installed_hook_path(label).replace(
                "\\", "/").endswith(f"/{label}")


class TestTheHooksFailClosed:
    """MUT-CI-03. The interpreter search finds nothing, and the hook refuses.

    Both hooks are shell scripts, so this runs them the way git does rather
    than reasoning about their text.
    """

    @pytest.mark.skipif(shutil.which("sh") is None,
                        reason="no POSIX shell on this machine")
    @pytest.mark.parametrize("label", ["pre-commit", "pre-push"])
    def test_no_interpreter_refuses(self, tmp_path: Path, label: str) -> None:
        env = dict(os.environ)
        # An empty PATH and no venv beside it: `command -v` finds nothing, and
        # the loop falls through to the refusal at the end.
        env["PATH"] = str(tmp_path)
        completed = subprocess.run(
            [shutil.which("sh"), str(_HOOKS / label)],
            cwd=str(tmp_path), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False)

        assert completed.returncode != 0
        assert "no python interpreter found" in completed.stderr

    @pytest.mark.parametrize("label", ["pre-commit", "pre-push"])
    def test_the_refusal_names_where_it_looked(self, label: str) -> None:
        text = (_HOOKS / label).read_text(encoding="utf-8")
        assert "exit 1" in text
        assert "backend/.venv" in text


class TestTheBuildLaneReadsItsOwnSelftest:
    """The one step the owner added by hand, and it had no test at all.

    Deleting the whole build-and-selftest block left every test in this file
    green. The steps below are measured on the parsing, on the freshness
    check, and on the order the lane puts them in.
    """

    def test_every_flag_on_the_line_has_to_be_true(self) -> None:
        good = ('SELFTEST healthz=True root_serves_spa=True '
                'voice_payload=True status={"initialized":false}')
        flags, problem = run_gates.read_selftest_flags(good)
        assert problem == ""
        assert flags == {"healthz": True, "root_serves_spa": True,
                         "voice_payload": True}

    def test_one_false_flag_is_a_failure_that_names_it(self) -> None:
        bad = ('SELFTEST healthz=True root_serves_spa=True '
               'voice_payload=False status={}')
        flags, problem = run_gates.read_selftest_flags(bad)
        assert flags["voice_payload"] is False
        assert "voice_payload" in problem

    def test_a_flag_nobody_hardcoded_is_still_read(self) -> None:
        # The list of checks is NOT written down here. It was written down in
        # three files with nothing importing anything, so renaming a check in
        # run_app.py would have made this gate refuse every build forever.
        flags, problem = run_gates.read_selftest_flags(
            "SELFTEST healthz=True a_check_added_next_year=False status={}")
        assert problem != ""
        assert "a_check_added_next_year" in problem

    def test_the_words_in_a_traceback_are_not_a_selftest(self) -> None:
        # A substring search over the whole output was satisfied by anything
        # that quoted the words. The line is what reports, so the line is what
        # is read.
        noise = ('Traceback (most recent call last):\n'
                 '  File "x.py", line 1\n'
                 'RuntimeError: healthz=True root_serves_spa=True '
                 'voice_payload=True\n')
        _flags, problem = run_gates.read_selftest_flags(noise)
        assert "no SELFTEST line" in problem

    def test_a_line_with_nothing_to_read_is_a_failure(self) -> None:
        _flags, problem = run_gates.read_selftest_flags("SELFTEST status={}")
        assert "no checks" in problem

    def test_an_exe_older_than_the_build_is_refused(
        self, tmp_path: Path,
    ) -> None:
        """The failure that shipped in the first run of this lane.

        PyInstaller refused the build, produced nothing, and the selftest then
        started the PREVIOUS exe, read three True flags off it and printed
        "ok". A selftest of yesterday's artefact reads like proof and is not.
        """
        exe = tmp_path / "dist" / "Elysium.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"stale")

        assert run_gates._selftest(exe, time.time() + 60) is False

    def test_an_exe_that_is_not_there_is_refused(self, tmp_path: Path) -> None:
        assert run_gates._selftest(tmp_path / "nothing.exe", 0.0) is False


def _record_build_steps(tmp_path: Path,
                        monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Run the verification build with every step and the temp area faked.

    The area is redirected at tmp_path because this suite's fs_guard refuses
    a write into the real %TEMP%, and it is right to: a test that writes
    there is writing on the machine rather than in a fixture. What is under
    measurement is the PATHS the build steps are handed, not the build.
    """
    recorded: list[list[str]] = []

    class Area:
        def __enter__(self) -> str:
            return str(tmp_path / "outside")

        def __exit__(self, *_exc: object) -> None:
            return None

    def remember(label: str, argv: list[str], cwd: Path,
                 env: dict[str, str] | None = None) -> bool:
        recorded.append([label, *[str(a) for a in argv]])
        return True

    monkeypatch.setattr(run_gates.tempfile, "TemporaryDirectory",
                        lambda *a, **k: Area())
    monkeypatch.setattr(run_gates, "_run", remember)
    monkeypatch.setattr(run_gates, "_selftest", lambda exe, after: True)
    assert run_gates.verification_build(True) is True
    return recorded


class TestTheVerificationBuildStaysOutsideTheRepository:
    """The owner's ruling, 30 August 2026, as something measurable.

    Pre-push verification must not mutate, replace, regenerate, touch or
    otherwise alter any tracked release artifact or canonical build output.
    Freshness stays a release-gate question and must not be satisfied as a
    side effect of verification.
    """

    def test_no_step_writes_into_the_tree(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Read as data, not as prose: every output path the build steps are
        # given has to be outside the repository. The first draft ran
        # `npm run build`, which rewrote frontend/dist and made the NEXT
        # push's freshness check fail.
        recorded = _record_build_steps(tmp_path, monkeypatch)
        assert recorded, "the verification build ran no steps at all"

        repo = str(_REPO).lower()
        for label, *argv in recorded:
            for flag in ("--outDir", "--distpath", "--workpath"):
                if flag in argv:
                    target = argv[argv.index(flag) + 1]
                    assert not target.lower().startswith(repo), (
                        f"{label} writes {target} inside the repository")

        # GROUND: the steps that must be there ARE there, so this does not
        # pass because nothing ran.
        labels = " ".join(step[0] for step in recorded)
        assert "vite build" in labels
        assert "pyinstaller" in labels

    def test_the_output_paths_are_actually_named(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Without this, dropping --distpath would leave the exe in
        # backend/dist and the test above would still pass, because it only
        # checks the flags it finds.
        recorded = _record_build_steps(tmp_path, monkeypatch)
        flat = [token for step in recorded for token in step]
        for flag in ("--outDir", "--distpath", "--workpath"):
            assert flag in flat, (
                f"{flag} is gone, so that output lands back in the tree")

    def test_the_freshness_test_is_named_and_still_exists(self) -> None:
        # It is deselected from this lane on purpose. That is only honest
        # while the test is still there to run everywhere else.
        path, _, leaf = run_gates.FRESHNESS_TEST.partition("::")
        source = (_REPO / "backend" / path).read_text(encoding="utf-8")
        assert f"def {leaf}(" in source, (
            "the lane deselects a test that no longer exists, which means "
            "freshness is now checked by nothing at all")

    def test_the_build_environment_drops_foreign_path_entries(self) -> None:
        """The spec refuses a build that would ship somebody else's DLLs.

        On this machine PATH carried a LaTeX distribution with its own copies
        of the UCRT runtime, PyInstaller resolves DLLs through PATH, and the
        spec's guard stopped the build. The lane cuts PATH back to this
        project rather than widening the guard.
        """
        env = run_gates._build_environment()
        entries = [e for e in env["PATH"].split(os.pathsep) if e.strip()]
        assert entries, "the trimmed PATH kept nothing at all"

        repo = str(run_gates.REPO_ROOT).lower()
        system = os.environ.get("SystemRoot", r"C:\Windows").lower()
        venv = str(Path(sys.prefix)).lower()
        for entry in entries:
            low = entry.lower()
            assert low.startswith((repo, system, venv)), entry

        # GROUND CONTROL: something WAS dropped, so this is not a function
        # that hands PATH back unchanged.
        original = [e for e in os.environ.get("PATH", "").split(os.pathsep)
                    if e.strip()]
        assert len(entries) < len(original)


class TestTheLanesAreWiredToTheRightEntryPoint:
    @pytest.mark.parametrize("label,lane", [("pre-commit", "pre-commit"),
                                            ("pre-push", "pre-push")])
    def test_each_hook_asks_for_its_own_lane(self, label: str,
                                             lane: str) -> None:
        # Not a text check standing in for behaviour: the argument is the
        # contract between the hook and run_gates.py, and run_gates rejects an
        # unknown one. Both halves are measured, here and below.
        text = (_HOOKS / label).read_text(encoding="utf-8")
        assert f"--hook {lane}" in text

    def test_the_runner_refuses_a_lane_it_does_not_have(self) -> None:
        assert run_gates.main(["--hook", "post-checkout"]) == 2
        assert run_gates.main([]) == 2
        assert run_gates.main(["--hook"]) == 2

    def test_the_runner_knows_exactly_these_two_lanes(self) -> None:
        assert set(run_gates.LANES) == {"pre-commit", "pre-push"}
