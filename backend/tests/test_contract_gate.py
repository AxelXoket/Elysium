"""The proof gate's own guard.

_contract_gate.py decides whether a registered node id counts as evidence for
a promise in README.md or SECURITY.md. Four of the decisions it makes are
arguments written in its docstring, and an argument in a docstring is worth
nothing: each one is a line somebody can delete without any test going red.

  * a SKIPPED test is not a passing test;
  * the gate never starts a child pytest from inside one of its own children;
  * a junit case is matched to the node id that selected it by the whole leaf
    name, so `test_a` does not swallow `test_ab`;
  * a node id pytest cannot collect is dropped BEFORE the run, because handing
    an unknown id to pytest makes it refuse the whole batch, and every other
    proof in that batch would read as missing.

Every test below builds a synthetic tree under tmp_path and measures the gate
against it. Nothing is written into this repository, and nothing here reads
the gate's source text: the subject is the answer the gate gives.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests import _contract_gate

_PASSING = "def test_it_passes():\n    assert True\n"


def _tree(root: Path, **modules: str) -> Path:
    """A synthetic tests/ package pytest can be pointed at."""
    tests = root / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    for name, body in modules.items():
        (tests / f"{name}.py").write_text(body, encoding="utf-8")
    return root


class TestASkipIsNotAPass:
    """A skipped test proves nothing about the promise it is registered to.

    Reading a skip as a pass is the same mistake the old gate made one level
    up, when it read "this attribute is callable" as "this test passes".
    """

    def test_a_skipped_test_is_not_a_proof(self, tmp_path: Path) -> None:
        root = _tree(
            tmp_path,
            test_skipped=(
                "import pytest\n"
                "\n"
                "@pytest.mark.skip(reason='not today')\n"
                "def test_it_is_skipped():\n"
                "    assert True\n"
            ),
        )
        assert _contract_gate.resolve_backend(
            "tests/test_skipped.py::test_it_is_skipped", root) is False

    def test_a_passing_test_beside_it_still_is(self, tmp_path: Path) -> None:
        # GROUND CONTROL for the test above. Without it, a gate that answered
        # False to everything would satisfy the skip case perfectly.
        root = _tree(tmp_path, test_green=_PASSING)
        assert _contract_gate.resolve_backend(
            "tests/test_green.py::test_it_passes", root) is True


class TestItDoesNotCallItselfForever:
    """The gate runs pytest. Inside that pytest, it must refuse to run pytest.

    A proof registered against a test in one of the contract files would
    otherwise open a chain of child processes with nothing at the end of it.
    """

    def test_a_child_refuses_to_start_a_grandchild(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root = _tree(tmp_path, test_green=_PASSING)
        monkeypatch.setenv(_contract_gate.DEPTH_ENV, "1")

        with pytest.raises(RuntimeError, match="recursion"):
            _contract_gate.resolve_backend(
                "tests/test_green.py::test_it_passes", root)

    def test_the_same_call_outside_a_child_is_answered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # GROUND CONTROL: the guard is the environment variable, not the call.
        root = _tree(tmp_path, test_green=_PASSING)
        monkeypatch.delenv(_contract_gate.DEPTH_ENV, raising=False)

        assert _contract_gate.resolve_backend(
            "tests/test_green.py::test_it_passes", root) is True

    def test_a_child_reports_itself_as_one(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv(_contract_gate.DEPTH_ENV, raising=False)
        assert _contract_gate.running_as_gate_child() is False
        monkeypatch.setenv(_contract_gate.DEPTH_ENV, "1")
        assert _contract_gate.running_as_gate_child() is True

    def test_an_explicit_zero_does_not_arm_the_guard(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The variable is a depth counter. Reading any value at all as "yes"
        # would let `DEPTH_ENV=0` switch the whole registry off, which is the
        # opposite of what the name promises.
        for off in ("0", "", "  "):
            monkeypatch.setenv(_contract_gate.DEPTH_ENV, off)
            assert _contract_gate.running_as_gate_child() is False


class TestAnInfrastructureFaultIsNotAVerdict:
    """"The run did not happen" and "the proof does not exist" are not the
    same sentence, and the gate must not say the second when it means the
    first. One bad batch would otherwise report a whole registry as broken.
    """

    def test_a_collection_that_matched_nothing_raises_rather_than_reports(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The shape a moved rootdir has: pytest succeeds and collects, but
        # spells every node id with a prefix the registry does not use.
        root = _tree(tmp_path, test_green=_PASSING)
        real = _contract_gate._pytest

        def misspelled(backend_root, args):
            if "--collect-only" in args:
                completed = real(backend_root, args)
                completed.stdout = (
                    "backend/tests/test_green.py::test_it_passes\n"
                    "\n1 test collected in 0.01s\n"
                )
                return completed
            return real(backend_root, args)

        monkeypatch.setattr(_contract_gate, "_pytest", misspelled)
        with pytest.raises(RuntimeError, match="collected nothing"):
            _contract_gate.resolve_backend(
                "tests/test_green.py::test_it_passes", root)

    def test_a_run_that_wrote_no_report_raises_rather_than_reports(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root = _tree(tmp_path, test_green=_PASSING)
        real = _contract_gate._pytest

        def no_report(backend_root, args):
            if "--collect-only" in args:
                return real(backend_root, args)
            return subprocess.CompletedProcess(args, 3, "crashed", "")

        monkeypatch.setattr(_contract_gate, "_pytest", no_report)
        with pytest.raises(RuntimeError, match="wrote no report"):
            _contract_gate.resolve_backend(
                "tests/test_green.py::test_it_passes", root)

    def test_a_file_with_no_tests_in_it_is_answered_not_raised(
        self, tmp_path: Path,
    ) -> None:
        # GROUND CONTROL for both cases above. An empty test module and a
        # module that will not import are real answers about the registry,
        # not infrastructure faults, and must stay quiet.
        root = _tree(
            tmp_path,
            test_nothing_here="def helper():\n    return 1\n",
        )
        assert _contract_gate.resolve_backend(
            "tests/test_nothing_here.py::test_missing", root) is False


class TestOneNodeIdHasOneSpelling:
    """`tests\\test_x.py` and `tests/test_x.py` are the same test.

    Path.is_file() accepts both, pytest prints only one of them, and the gate
    decides collectability by matching what pytest printed. So the natural
    Windows spelling, and a shell's `./` prefix, resolved to nothing pytest had
    said and were rewarded with the infrastructure error that names the whole
    registry as unmeasurable. Measured, not imagined.
    """

    @pytest.mark.parametrize("spelling", [
        "tests/test_green.py::test_it_passes",
        "tests\\test_green.py::test_it_passes",
        "./tests/test_green.py::test_it_passes",
        "tests//test_green.py::test_it_passes",
    ])
    def test_every_spelling_answers_the_same(
        self, tmp_path: Path, spelling: str,
    ) -> None:
        root = _tree(tmp_path, test_green=_PASSING)
        assert _contract_gate.resolve_backend(spelling, root) is True

    def test_a_different_test_is_still_a_different_test(
        self, tmp_path: Path,
    ) -> None:
        # GROUND: normalising the separators must not normalise away the name.
        root = _tree(tmp_path, test_green=_PASSING)
        assert _contract_gate.resolve_backend(
            "tests\\test_green.py::test_something_else", root) is False


class TestTheChildIsToldItIsOne:
    """The arming half of the recursion promise.

    The guard that refuses to start a grandchild is tested elsewhere. What is
    tested here is that the parent actually sets the variable the guard reads:
    deleting that one line left every test in this file green while the
    promise it makes was gone.
    """

    def test_the_child_sees_the_depth_variable(self, tmp_path: Path) -> None:
        root = _tree(
            tmp_path,
            test_depth=(
                "import os\n"
                "\n"
                "def test_the_gate_armed_me():\n"
                f"    assert os.environ.get({_contract_gate.DEPTH_ENV!r})\n"
            ),
        )
        assert _contract_gate.resolve_backend(
            "tests/test_depth.py::test_the_gate_armed_me", root) is True

    def test_a_child_that_demands_the_absence_of_it_goes_red(
        self, tmp_path: Path,
    ) -> None:
        # GROUND, and the discriminator: if the parent stopped setting the
        # variable, the test above would still pass for the wrong reason
        # unless something also measures the opposite.
        root = _tree(
            tmp_path,
            test_nodepth=(
                "import os\n"
                "\n"
                "def test_nothing_armed_me():\n"
                f"    assert not os.environ.get({_contract_gate.DEPTH_ENV!r})\n"
            ),
        )
        assert _contract_gate.resolve_backend(
            "tests/test_nodepth.py::test_nothing_armed_me", root) is False


class TestTheShellDoesNotGetAVote:
    """One export in the developer's shell used to answer for the registry."""

    def test_pytest_addopts_cannot_change_the_verdict(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # --collect-only makes every child run zero tests, so every proof read
        # as missing. Measured before the scrub went in: 172 claims, all
        # "no proof", from one variable.
        root = _tree(tmp_path, test_green=_PASSING)
        monkeypatch.setenv("PYTEST_ADDOPTS", "--collect-only")
        assert _contract_gate.resolve_backend(
            "tests/test_green.py::test_it_passes", root) is True

    def test_pytest_plugins_cannot_either(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root = _tree(tmp_path, test_green=_PASSING)
        monkeypatch.setenv("PYTEST_PLUGINS", "a_plugin_that_does_not_exist")
        assert _contract_gate.resolve_backend(
            "tests/test_green.py::test_it_passes", root) is True


class TestEveryCaseHasToPass:
    """A parametrised proof is green only when all of its cases are.

    Reading "one of them passed" as evidence is how a guarantee that is half
    broken reads as proven.
    """

    def test_one_red_parameter_makes_the_whole_proof_red(
        self, tmp_path: Path,
    ) -> None:
        root = _tree(
            tmp_path,
            test_mixed=(
                "import pytest\n"
                "\n"
                "@pytest.mark.parametrize('n', [1, 0])\n"
                "def test_p(n):\n"
                "    assert n\n"
            ),
        )
        assert _contract_gate.resolve_backend(
            "tests/test_mixed.py::test_p", root) is False

    def test_all_green_parameters_still_pass(self, tmp_path: Path) -> None:
        # GROUND CONTROL for the AND above.
        root = _tree(
            tmp_path,
            test_all_green=(
                "import pytest\n"
                "\n"
                "@pytest.mark.parametrize('n', [1, 2])\n"
                "def test_p(n):\n"
                "    assert n\n"
            ),
        )
        assert _contract_gate.resolve_backend(
            "tests/test_all_green.py::test_p", root) is True


class TestTwoFilesWithTheSameTestName:
    """The half of the junit mapping that the module path carries.

    Every synthetic tree here used to be one module, so the class name was
    never the thing telling two cases apart, and dropping it from the
    comparison changed nothing.
    """

    def test_a_red_test_does_not_poison_its_namesake_elsewhere(
        self, tmp_path: Path,
    ) -> None:
        root = _tree(
            tmp_path,
            test_here="def test_same_name():\n    assert True\n",
            test_there="def test_same_name():\n    assert False\n",
        )
        nodes = (
            "tests/test_here.py::test_same_name",
            "tests/test_there.py::test_same_name",
        )
        assert _contract_gate.resolve_backend(
            nodes[0], root, prime=lambda: nodes) is True
        assert _contract_gate.resolve_backend(
            nodes[1], root, prime=lambda: nodes) is False


class TestAnExpectedFailureIsNotAProof:
    def test_an_xfail_that_passes_is_not_evidence(
        self, tmp_path: Path,
    ) -> None:
        # Its own author declared it broken. Passing anyway is news, not proof.
        root = _tree(
            tmp_path,
            test_xpass=(
                "import pytest\n"
                "\n"
                "@pytest.mark.xfail(reason='known broken')\n"
                "def test_it_actually_passes():\n"
                "    assert True\n"
            ),
        )
        assert _contract_gate.resolve_backend(
            "tests/test_xpass.py::test_it_actually_passes", root) is False

    def test_an_xfail_that_fails_is_not_evidence_either(
        self, tmp_path: Path,
    ) -> None:
        root = _tree(
            tmp_path,
            test_xfail=(
                "import pytest\n"
                "\n"
                "@pytest.mark.xfail(reason='known broken')\n"
                "def test_it_fails():\n"
                "    assert False\n"
            ),
        )
        assert _contract_gate.resolve_backend(
            "tests/test_xfail.py::test_it_fails", root) is False

    def test_an_ordinary_pass_beside_them_still_is(
        self, tmp_path: Path,
    ) -> None:
        # GROUND CONTROL: xfail_strict does not make everything red.
        root = _tree(tmp_path, test_green=_PASSING)
        assert _contract_gate.resolve_backend(
            "tests/test_green.py::test_it_passes", root) is True


class TestTheRootIsPinned:
    """A config file one directory up used to rename every node id.

    pytest spells node ids relative to the rootdir it infers, and it infers
    upward. The day a pyproject.toml lands at the repository root, every
    registered proof stops matching what pytest prints.
    """

    def test_a_config_file_above_the_tests_changes_nothing(
        self, tmp_path: Path,
    ) -> None:
        repo = tmp_path / "repo"
        backend = repo / "backend"
        _tree(backend, test_probe=_PASSING)
        (repo / "pyproject.toml").write_text(
            "[tool.pytest.ini_options]\nminversion = '7.0'\n",
            encoding="utf-8")

        assert _contract_gate.resolve_backend(
            "tests/test_probe.py::test_it_passes", backend) is True


class TestTheBatchSplitter:
    def test_it_never_exceeds_the_command_line_budget(self) -> None:
        items = [f"tests/test_{i:04d}.py::test_something_long_enough" for i in
                 range(2000)]
        chunks = _contract_gate._chunks(items)
        assert [i for chunk in chunks for i in chunk] == items
        for chunk in chunks:
            assert sum(len(i) + 1 for i in chunk) <= (
                _contract_gate._MAX_COMMAND_CHARS + max(len(i) for i in chunk))

    def test_an_empty_list_produces_no_chunks(self) -> None:
        assert _contract_gate._chunks([]) == []

    def test_a_single_item_is_never_dropped(self) -> None:
        # Even one longer than the budget: it cannot be split, and losing it
        # silently would be worse than a command line the shell refuses.
        huge = "tests/" + "x" * 30000 + ".py::test_x"
        assert _contract_gate._chunks([huge]) == [[huge]]


class TestOneNameDoesNotSwallowAnother:
    """`test_a` and `test_ab` are different tests with the same prefix.

    junit reports a parametrised case as `test_a[x]`, so the leaf has to be
    matched by equality or by an opening bracket. Matching by bare prefix
    lets a neighbour's verdict decide this one's.
    """

    def test_a_prefix_neighbours_failure_is_not_this_tests_failure(
        self, tmp_path: Path,
    ) -> None:
        root = _tree(
            tmp_path,
            test_neighbours=(
                "def test_a():\n"
                "    assert True\n"
                "\n"
                "def test_ab():\n"
                "    assert False\n"
            ),
        )
        nodes = (
            "tests/test_neighbours.py::test_a",
            "tests/test_neighbours.py::test_ab",
        )
        # One batch, so both verdicts come out of the same junit report and
        # the mapping is what separates them.
        assert _contract_gate.resolve_backend(
            nodes[0], root, prime=lambda: nodes) is True
        assert _contract_gate.resolve_backend(
            nodes[1], root, prime=lambda: nodes) is False

    def test_a_parametrised_case_still_belongs_to_its_node_id(
        self, tmp_path: Path,
    ) -> None:
        # The registry never spells out the bracketed suffix, so the leaf has
        # to match `test_p[1]` while still not matching `test_ping`.
        root = _tree(
            tmp_path,
            test_params=(
                "import pytest\n"
                "\n"
                "@pytest.mark.parametrize('n', [1, 2])\n"
                "def test_p(n):\n"
                "    assert n\n"
                "\n"
                "def test_ping():\n"
                "    assert False\n"
            ),
        )
        nodes = (
            "tests/test_params.py::test_p",
            "tests/test_params.py::test_ping",
        )
        assert _contract_gate.resolve_backend(
            nodes[0], root, prime=lambda: nodes) is True
        assert _contract_gate.resolve_backend(
            nodes[1], root, prime=lambda: nodes) is False


class TestAnUncollectableNameDoesNotSinkTheBatch:
    """The reason collection is measured BEFORE anything is run.

    Hand pytest a node id it cannot resolve and it refuses the whole
    invocation: no tests run, no report is written, and every proof in that
    batch reads as missing. The registry would then report a hundred and
    fifty broken promises because one name was renamed.
    """

    def test_one_bad_name_does_not_take_the_good_ones_with_it(
        self, tmp_path: Path,
    ) -> None:
        root = _tree(tmp_path, test_green=_PASSING)
        nodes = (
            "tests/test_green.py::test_it_passes",
            "tests/test_green.py::test_was_renamed_away",
        )
        assert _contract_gate.resolve_backend(
            nodes[0], root, prime=lambda: nodes) is True
        assert _contract_gate.resolve_backend(
            nodes[1], root, prime=lambda: nodes) is False

    def test_a_module_that_cannot_even_be_imported_is_not_a_proof(
        self, tmp_path: Path,
    ) -> None:
        root = _tree(
            tmp_path,
            test_broken="import a_module_that_is_not_installed_anywhere\n",
            test_green=_PASSING,
        )
        nodes = (
            "tests/test_green.py::test_it_passes",
            "tests/test_broken.py::test_anything",
        )
        assert _contract_gate.resolve_backend(
            nodes[1], root, prime=lambda: nodes) is False
        # GROUND CONTROL: the collection error next to it did not take the
        # healthy module down with it.
        assert _contract_gate.resolve_backend(
            nodes[0], root, prime=lambda: nodes) is True

    def test_a_file_that_is_not_there_at_all_is_not_a_proof(
        self, tmp_path: Path,
    ) -> None:
        root = _tree(tmp_path, test_green=_PASSING)
        assert _contract_gate.resolve_backend(
            "tests/test_nowhere.py::test_nothing", root) is False


class TestTheFrontendHalfIsTextOnlyAndSaysSo:
    """The delimiter rule, measured on synthetic files rather than on the repo.

    The repository copies of these cases live in the two contract files, where
    they read the real static-safety.test.ts. These are the same rule with the
    artefact under this test's own control.
    """

    def test_the_name_has_to_be_followed_by_a_quote_or_a_colon(
        self, tmp_path: Path,
    ) -> None:
        probe = tmp_path / "probe.test.ts"
        probe.write_text(
            'it("S-40b: the neighbour", () => {});\n'
            'it("plain english name", () => {});\n',
            encoding="utf-8")

        assert _contract_gate.resolve_frontend(
            "probe.test.ts::S-40b", tmp_path) is True
        assert _contract_gate.resolve_frontend(
            "probe.test.ts::plain english name", tmp_path) is True
        assert _contract_gate.resolve_frontend(
            "probe.test.ts::S-40", tmp_path) is False

    def test_a_quoted_mention_outside_a_call_is_not_a_test(
        self, tmp_path: Path,
    ) -> None:
        """The LEFT anchor, measured on its own.

        The name is inside a quoted string here, so dropping the opening
        `("` and matching from the quote alone would resolve it. A comment
        would not: comments carry no quote, so a comment probe cannot tell
        the two rules apart and is not the test for this.
        """
        probe = tmp_path / "probe.test.ts"
        probe.write_text(
            'const note = "S-41: deleted in an earlier pass";\n'
            '// S-41: and mentioned again in prose\n',
            encoding="utf-8")
        assert _contract_gate.resolve_frontend(
            "probe.test.ts::S-41", tmp_path) is False

    def test_a_test_vitest_will_never_run_is_not_a_proof(
        self, tmp_path: Path,
    ) -> None:
        """`it.skip` and `it.todo` are the vitest spelling of a skipped node.

        Text matching cannot see a vitest result, but it can see that this
        one is switched off, and a switched-off test proves nothing about the
        promise it is registered against.
        """
        probe = tmp_path / "probe.test.ts"
        probe.write_text(
            'it.skip("S-43: switched off", () => {});\n'
            'it.todo("S-44: not written yet");\n'
            'it.only("S-45: the one being worked on", () => {});\n'
            'it("S-46: an ordinary test", () => {});\n',
            encoding="utf-8")

        assert _contract_gate.resolve_frontend(
            "probe.test.ts::S-43", tmp_path) is False
        assert _contract_gate.resolve_frontend(
            "probe.test.ts::S-44", tmp_path) is False
        # GROUND CONTROL, both halves: `.only` still runs, and so does the
        # plain form. A resolver that refused everything with a dot in front
        # of it would pass the two above and fail these.
        assert _contract_gate.resolve_frontend(
            "probe.test.ts::S-45", tmp_path) is True
        assert _contract_gate.resolve_frontend(
            "probe.test.ts::S-46", tmp_path) is True

    def test_a_suite_title_is_not_a_test(self, tmp_path: Path) -> None:
        """A registered node id names a test, never the suite around it.

        An empty `describe` carrying the right title would otherwise read as
        a proof, and an empty describe runs nothing at all.
        """
        probe = tmp_path / "probe.test.ts"
        probe.write_text(
            'describe("S-48: a suite with nothing in it", () => {});\n',
            encoding="utf-8")
        assert _contract_gate.resolve_frontend(
            "probe.test.ts::S-48", tmp_path) is False

    def test_a_title_parked_in_a_variable_is_not_a_test(
        self, tmp_path: Path,
    ) -> None:
        """The parenthesis has to belong to a call.

        `const banner = ("S-49: ...")` puts the name in quotes right after an
        opening bracket and would satisfy a rule that only looked for `("`.
        """
        probe = tmp_path / "probe.test.ts"
        probe.write_text(
            'const banner = ("S-49: not a test at all");\n',
            encoding="utf-8")
        assert _contract_gate.resolve_frontend(
            "probe.test.ts::S-49", tmp_path) is False

    def test_an_it_each_template_still_resolves(self, tmp_path: Path) -> None:
        # GROUND CONTROL for the two above: `it.each` closes its array before
        # the call parenthesis, so the character in front of `(` is `)`. A
        # rule that demanded an identifier there would drop every table test
        # in the suite, including one that is registered today.
        probe = tmp_path / "probe.test.ts"
        probe.write_text(
            'it.each([\n  "a",\n  "b",\n])("refuses %s", (x) => {});\n',
            encoding="utf-8")
        assert _contract_gate.resolve_frontend(
            "probe.test.ts::refuses %s", tmp_path) is True

    def test_a_name_that_is_skipped_once_and_run_once_still_resolves(
        self, tmp_path: Path,
    ) -> None:
        # The scan does not stop at the first match: one disabled spelling
        # must not hide a live one further down the file.
        probe = tmp_path / "probe.test.ts"
        probe.write_text(
            'it.skip("S-47: the old one", () => {});\n'
            'it("S-47: the replacement", () => {});\n',
            encoding="utf-8")
        assert _contract_gate.resolve_frontend(
            "probe.test.ts::S-47", tmp_path) is True

    def test_a_file_that_is_not_there_resolves_to_nothing(
        self, tmp_path: Path,
    ) -> None:
        assert _contract_gate.resolve_frontend(
            "nowhere.test.ts::S-42", tmp_path) is False
