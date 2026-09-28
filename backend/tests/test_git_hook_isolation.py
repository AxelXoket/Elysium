"""A test run started from a git hook cannot write into the repository.

What happened: the pre-push hook ran the backend suite from a linked worktree
with GIT_DIR exported. The `repo` fixture in test_hygiene_gate.py ran
`git init`, `git config user.*` and `git commit` in tmp_path with the
inherited environment, and every one of them went to the repository GIT_DIR
named: core.bare=true, a test identity and core.hooksPath in its config, and
test commits on the branch being pushed.

How it is replayed here, without going anywhere near the real repository: a
throwaway repository in tmp_path plays the part of the real one, and the test
points GIT_DIR (and the other hook variables) at it the way a hook would. The
scratch-repository path then runs, and the stand-in must come out byte for
byte as it went in.

Two walls are measured, separately, because each has to hold on its own:

  _git_env        what every git call in the suite runs under
  run_gates.py    what every process the hook lane starts runs under

Each has a positive control that switches that one wall off and watches the
stand-in get written, so a green result here means the wall held, not that
the scenario never reached git.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests import _git_env

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "verify"))

import run_gates  # noqa: E402

#: The identity the scratch path writes: the mark a leak leaves behind.
LEAKED_EMAIL = _git_env.SCRATCH_EMAIL

#: The shapes of environment a hook hands its children. pre-push exports
#: GIT_DIR; pre-commit adds GIT_INDEX_FILE; from a linked worktree git can
#: also export the work tree and the common directory.
HOOK_SHAPES = {
    "pre-push": ("GIT_DIR",),
    "pre-commit": ("GIT_DIR", "GIT_INDEX_FILE"),
    "linked-worktree": ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE",
                        "GIT_COMMON_DIR"),
}


def _stand_in(tmp_path: Path) -> Path:
    """A normal repository with one commit, in the role of the real one."""
    repo = _git_env.init_scratch_repo(tmp_path / "stand-in")
    (repo / "README").write_text("the real repository\n", encoding="utf-8")
    _git_env.run_git("add", "README", cwd=repo, check=True,
                     capture_output=True)
    _git_env.run_git("-c", "user.email=owner@example.invalid",
                     "-c", "user.name=owner", "commit", "-q", "-m", "first",
                     cwd=repo, check=True, capture_output=True)
    # GROUND: the stand-in starts without the mark the leak would leave, or
    # finding it afterwards would mean nothing. init_scratch_repo wrote that
    # very identity, so it comes out again before anything is measured.
    _git_env.run_git("config", "--unset", "user.email", cwd=repo, check=True)
    _git_env.run_git("config", "--unset", "user.name", cwd=repo, check=True)
    config = (repo / ".git" / "config").read_text(encoding="utf-8")
    assert LEAKED_EMAIL not in config
    return repo


def _fingerprint(root: Path) -> dict[str, str]:
    """md5 of every file under `root`, keyed by relative path."""
    return {
        p.relative_to(root).as_posix(): hashlib.md5(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def _point_hook_at(monkeypatch: pytest.MonkeyPatch, stand_in: Path,
                   names: tuple[str, ...]) -> None:
    git_dir = stand_in / ".git"
    values = {
        "GIT_DIR": str(git_dir),
        "GIT_INDEX_FILE": str(git_dir / "index"),
        "GIT_WORK_TREE": str(stand_in),
        "GIT_COMMON_DIR": str(git_dir),
    }
    for name in names:
        monkeypatch.setenv(name, values[name])


def _hygiene_fixture_path(scratch: Path) -> None:
    """What test_hygiene_gate.py's `repo` fixture and its tests do to git.

    The same calls, through the same helper: init_scratch_repo is the fixture
    body, and add plus commit is what its tests do after it.
    """
    _git_env.init_scratch_repo(scratch)
    (scratch / "notes.md").write_text("add\n", encoding="utf-8")
    _git_env.run_git("add", "notes.md", cwd=scratch, check=True,
                     capture_output=True)
    _git_env.run_git("commit", "-q", "-m", "add", cwd=scratch, check=True,
                     capture_output=True)


def _scratch_has_its_own_commit(scratch: Path) -> bool:
    if not (scratch / ".git").is_dir():
        return False
    out = _git_env.run_git("log", "--format=%s", cwd=scratch,
                           capture_output=True, text=True)
    return out.returncode == 0 and out.stdout.split() == ["add"]


class TestTheScrubList:
    def test_it_covers_every_variable_git_clears_for_a_submodule(
        self, tmp_path
    ):
        # Asked of the installed git rather than retyped: a git release that
        # adds a redirecting variable turns this red instead of slipping by.
        out = _git_env.run_git("rev-parse", "--local-env-vars", cwd=tmp_path,
                               capture_output=True, text=True, check=True)
        git_says = set(out.stdout.split())
        # GROUND: git answered with the list, not with nothing.
        assert "GIT_DIR" in git_says and len(git_says) >= 10, git_says
        missing = git_says - _git_env.scrub.REDIRECTING
        assert not missing, f"git redirects through {sorted(missing)} too"

    @pytest.mark.parametrize("name", [
        "GIT_DIR", "git_dir", "Git_Index_File", "GIT_COMMON_DIR",
        "GIT_WORK_TREE", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_3", "GIT_NAMESPACE",
    ])
    def test_a_redirecting_name_is_dropped_in_any_case(self, name):
        env = _git_env.scrub.without_redirects({name: "x", "KEEP": "1"})
        assert env == {"KEEP": "1"}

    @pytest.mark.parametrize("name", [
        "GIT_CEILING_DIRECTORIES", "GIT_CONFIG_NOSYSTEM", "GIT_TERMINAL_PROMPT",
        "GIT_EDITOR", "PATH",
    ])
    def test_a_variable_that_only_narrows_is_kept(self, name):
        # POSITIVE CONTROL for the one above: a scrub that dropped every GIT_*
        # name would pass that test and still be wrong.
        env = _git_env.scrub.without_redirects({name: "x"})
        assert env == {name: "x"}

    def test_the_suite_environment_never_reads_the_owners_config(self):
        env = _git_env.git_env()
        home = _git_env.empty_home()
        assert env["HOME"] == home
        assert env["GIT_CONFIG_GLOBAL"].startswith(home)
        assert env["GIT_CONFIG_NOSYSTEM"] == "1"
        assert Path(home).is_dir()
        assert Path(home).resolve() != Path.home().resolve()

    def test_the_run_wide_strip_removes_a_hook_variable(self, monkeypatch):
        # conftest calls this once at import, for pytest started from a hook
        # some way other than run_gates.py.
        monkeypatch.setenv("GIT_DIR", "somewhere")
        monkeypatch.setenv("GIT_CEILING_DIRECTORIES", "kept")
        removed = _git_env.strip_process_redirects()
        assert [n.upper() for n in removed] == ["GIT_DIR"]
        assert "GIT_DIR" not in os.environ
        # POSITIVE CONTROL: it strips the redirects, not the environment.
        assert os.environ["GIT_CEILING_DIRECTORIES"] == "kept"

    def test_run_git_refuses_an_environment_from_the_caller(self, tmp_path):
        with pytest.raises(TypeError):
            _git_env.run_git("status", cwd=tmp_path, env=dict(os.environ))


class TestTheSuiteHelperHoldsInsideAHook:
    @pytest.mark.parametrize("shape", sorted(HOOK_SHAPES))
    def test_the_real_repository_is_untouched(self, tmp_path, monkeypatch,
                                              shape):
        stand_in = _stand_in(tmp_path)
        before = _fingerprint(stand_in)
        _point_hook_at(monkeypatch, stand_in, HOOK_SHAPES[shape])

        scratch = tmp_path / "scratch"
        _hygiene_fixture_path(scratch)

        assert _fingerprint(stand_in) == before, (
            "the scratch path wrote into the repository the hook named")
        # GROUND: the path did its work somewhere, namely where it was told.
        assert _scratch_has_its_own_commit(scratch)

    @pytest.mark.parametrize("shape", sorted(HOOK_SHAPES))
    def test_without_the_scrub_the_same_path_writes_into_it(
        self, tmp_path, monkeypatch, shape
    ):
        """POSITIVE CONTROL. The scenario really reaches the stand-in.

        Only the redirect scrub is switched off; the config isolation stays,
        so the stand-in is still the only repository anything can touch.
        """
        stand_in = _stand_in(tmp_path)
        before = _fingerprint(stand_in)
        _point_hook_at(monkeypatch, stand_in, HOOK_SHAPES[shape])
        monkeypatch.setattr(_git_env.scrub, "without_redirects",
                            lambda env=None: dict(env if env is not None
                                                  else os.environ))

        scratch = tmp_path / "scratch"
        try:
            _hygiene_fixture_path(scratch)
        except subprocess.CalledProcessError:
            # With GIT_WORK_TREE pointed at the stand-in, `git add` from the
            # scratch folder is refused as outside the work tree. By then
            # init and config have already written into the stand-in, which
            # is what this measures.
            pass

        assert _fingerprint(stand_in) != before
        config = (stand_in / ".git" / "config").read_text(encoding="utf-8")
        assert LEAKED_EMAIL in config, config

    def test_the_gate_the_fixture_serves_reads_the_scratch_repository(
        self, tmp_path, monkeypatch
    ):
        """verify_hygiene starts git with whatever os.environ holds.

        So the fixture has to scrub os.environ itself, not only its own calls,
        or the gate under test reads the hook's repository while the test
        believes it is reading tmp_path.
        """
        from tests.test_hygiene_gate import hygiene

        stand_in = _stand_in(tmp_path)
        _point_hook_at(monkeypatch, stand_in, HOOK_SHAPES["pre-commit"])
        _git_env.isolate_process(monkeypatch)
        scratch = _git_env.init_scratch_repo(tmp_path / "scratch")
        (scratch / "only-here.md").write_text("x\n", encoding="utf-8")
        _git_env.run_git("add", "only-here.md", cwd=scratch, check=True)
        monkeypatch.setattr(hygiene, "REPO_ROOT", str(scratch))

        # The index mode, because that is where the two answers differ: the
        # stand-in's index has nothing staged, the scratch one has one file.
        # The working-tree mode would read the stand-in's listing and then
        # quietly skip every name it cannot find on disk under tmp_path, so
        # it passes either way and measures nothing.
        names = set(dict(hygiene.staged_files()))
        assert names == {"only-here.md"}, (
            f"the gate read some other index: {sorted(names)}")


_NAIVE_CHILD = """
import subprocess
# A test that forgot _git_env: plain git, inherited environment.
subprocess.run(["git", "init", "-q"], check=True)
subprocess.run(["git", "config", "user.email", "{email}"], check=True)
subprocess.run(["git", "config", "user.name", "t"], check=True)
"""


class TestTheHookLaneHoldsForATestThatForgets:
    def _run_naive_child(self, tmp_path, monkeypatch, shape,
                         handed) -> tuple[Path, dict, Path]:
        stand_in = _stand_in(tmp_path)
        before = _fingerprint(stand_in)
        # The child's git must not read the owner's config either. HOME and
        # NOSYSTEM survive the lane's scrub, so setting them here reaches it.
        _git_env.isolate_process(monkeypatch)
        _point_hook_at(monkeypatch, stand_in, HOOK_SHAPES[shape])
        scratch = tmp_path / "scratch"
        scratch.mkdir()
        # "explicit" is the pytest step of the push lane: it hands _run an
        # environment of its own (strict_lane_environment), built from
        # os.environ, and that one has to be scrubbed as well. Built here,
        # after the hook variables are set, or it would not carry them.
        env = None
        if handed == "explicit":
            env = run_gates.strict_lane_environment()
            assert "GIT_DIR" in env, "the handed environment has no hook in it"
        script = _NAIVE_CHILD.format(email=LEAKED_EMAIL)
        ok = run_gates._run("naive child", [sys.executable, "-c", script],
                            scratch, env=env)
        assert ok, "the child failed, so it proves nothing either way"
        return stand_in, before, scratch

    @pytest.mark.parametrize("shape", sorted(HOOK_SHAPES))
    @pytest.mark.parametrize("handed", ["inherited", "explicit"])
    def test_the_real_repository_is_untouched(self, tmp_path, monkeypatch,
                                              shape, handed):
        stand_in, before, scratch = self._run_naive_child(
            tmp_path, monkeypatch, shape, handed)
        assert _fingerprint(stand_in) == before
        # GROUND: the child's git ran, and wrote where it was started.
        config = (scratch / ".git" / "config").read_text(encoding="utf-8")
        assert LEAKED_EMAIL in config

    @pytest.mark.parametrize("shape", sorted(HOOK_SHAPES))
    @pytest.mark.parametrize("handed", ["inherited", "explicit"])
    def test_without_the_scrub_the_child_writes_into_it(
        self, tmp_path, monkeypatch, shape, handed
    ):
        # POSITIVE CONTROL: the lane's scrub is the only thing in the way.
        monkeypatch.setattr(run_gates, "child_environment",
                            lambda env=None: dict(env if env is not None
                                                  else os.environ))
        stand_in, before, scratch = self._run_naive_child(
            tmp_path, monkeypatch, shape, handed)
        assert _fingerprint(stand_in) != before
        config = (stand_in / ".git" / "config").read_text(encoding="utf-8")
        assert LEAKED_EMAIL in config
        assert not (scratch / ".git").exists()

    def test_the_strict_flag_survives_the_scrub(self, monkeypatch):
        # The scrub must take git's redirects and nothing the lane added.
        monkeypatch.setenv("GIT_DIR", "somewhere")
        env = run_gates.child_environment(run_gates.strict_lane_environment())
        assert "GIT_DIR" not in env
        for name, value in run_gates.STRICT_LANE.items():
            assert env[name] == value

    def test_the_selftest_child_is_scrubbed_too(self, tmp_path, monkeypatch):
        exe = tmp_path / "Elysium.exe"
        exe.write_bytes(b"not run")
        monkeypatch.setenv("GIT_DIR", str(tmp_path / "stand-in" / ".git"))
        seen: list[dict[str, str]] = []

        def fake_run(argv, **kwargs):
            seen.append(kwargs["env"])
            return subprocess.CompletedProcess(
                argv, 0, stdout="SELFTEST status=ok boot=True\n", stderr="")

        class Sandbox:
            def __enter__(self) -> str:
                return str(tmp_path / "sandbox")

            def __exit__(self, *_exc: object) -> None:
                return None

        monkeypatch.setattr(run_gates.tempfile, "TemporaryDirectory",
                            lambda *a, **k: Sandbox())
        monkeypatch.setattr(run_gates.subprocess, "run", fake_run)
        assert run_gates._selftest(exe, 0.0) is True
        (env,) = seen
        assert "GIT_DIR" not in env
        # GROUND: this is the selftest's environment, not an empty dict.
        assert env["ELYSIUM_SELFTEST"] == "1" and "PATH" in env
