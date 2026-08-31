"""The proof gate the two contract registries share.

Both test_privacy_contract.py and test_security_contract.py bind a documented
promise to a pytest node id. Until now the check behind that binding asked
only whether the name resolved to *something*: the module was imported, the
attributes were walked, and `callable(...)` decided the verdict. That accepted
three different non-proofs.

  * A test that exists and is RED. `callable` is true of a function that fails
    the moment it runs, so a broken guarantee read as proven.
  * A name pytest can never collect. `_resolve` itself is callable, and the
    old test asserted that as the positive control, which pinned the wrong
    behaviour in place: pytest collects `test_*` functions inside `test_*.py`
    files, not every callable attribute.
  * A frontend name that is only a PREFIX of a real one. The frontend half
    searched for `f'"{name}'` with no closing delimiter, so `"S-11` matched
    `it("S-11b: ...")` and deleting the S-11 rule would have left the registry
    still reporting it as present. static-safety.test.ts:761-772 documents
    that collision against itself.

So the gate here answers two questions per node id, and both have to be yes:

  (a) can pytest COLLECT it, and
  (b) is it GREEN today.

Both answers come from running pytest as a child process and caching the
verdicts for the rest of the session. A registry resolves in one pair of child
processes, one to collect and one to run, because the caller hands the whole
set of names it is about to ask for. Names outside a registry - the negative
controls the tests use - are measured one at a time, so a full run starts more
than two children even though no registered proof is measured twice.

A per-node `pytest.main` was rejected: it costs one full interpreter setup per
registered proof, and calling the running suite from inside itself is a
recursion waiting for the first proof that points at a contract file.

Not answered here: whether a proof goes RED under the mutation it names. That
is a schema question about the registry (a `mutation:` field, or a table beside
it) rather than a resolver question, and it belongs to the evidence-policy
unit, U-11. The invariant is written in three parts on purpose; this module
implements (a) and (b) and leaves (c) named rather than silently dropped.

A skipped test is NOT green. It proves nothing about the promise it is
registered against, and reading a skip as a pass is the same mistake as
reading `callable` as a pass, one level up.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable, Iterable

#: Set in the environment of every child pytest this module starts. The gate
#: refuses to run when it is already set, so a registered proof that points
#: back at a contract file cannot open an unbounded chain of subprocesses.
DEPTH_ENV = "ELYSIUM_CONTRACT_GATE_DEPTH"

#: A whole registry's worth of proofs runs in one child. The full backend
#: suite finishes in about eight minutes on this machine and a batch is a
#: fraction of it, so this is a hang detector rather than a budget.
_CHILD_TIMEOUT_SECONDS = 900

#: Windows caps a command line at 32767 characters. Node ids run long here
#: (a file path plus a class plus a method name), so a batch is split well
#: below that rather than discovering the ceiling on the day a claim is added.
_MAX_COMMAND_CHARS = 24000

#: (backend_root, node_id) -> verdict. Session-scoped: the registries resolve
#: the same names many times over (once per parametrised claim), and the
#: measurement does not change inside one run.
_VERDICTS: dict[tuple[str, str], bool] = {}

#: backend_root -> every node id --collect-only reported for it so far.
_COLLECTED: dict[str, set[str]] = {}

#: backend_root -> the files already surveyed, so a second batch over the same
#: files does not pay for collection twice.
_SURVEYED: dict[str, set[str]] = {}


def running_as_gate_child() -> bool:
    """True inside a pytest this module started.

    The variable is a depth COUNTER, so it is read as one. `0`, `00`, `0.0`,
    `false`, `no` and anything else that is not a positive number all mean
    "not a child": reading any value at all as yes would let `DEPTH_ENV=0`
    disarm the gate, which is the opposite of what the name says, and reading
    `false` as yes would do it with a word that means no.
    """
    raw = os.environ.get(DEPTH_ENV, "").strip()
    if not raw:
        return False
    try:
        return int(float(raw)) > 0
    except ValueError:
        return False


#: The answer at IMPORT time, frozen.
#:
#: The skip marker in the two contract files is evaluated when the module is
#: imported; the test that checks nobody has disarmed the gate runs later. A
#: pytest plugin that set the variable before import and cleared it during
#: collection therefore got both: every proof test skipped, and the guard that
#: was supposed to notice reporting a clean environment. Measured, not
#: imagined - it took two environment variables and ten lines.
#:
#: Both halves now read this one value, so the two can no longer disagree.
GATE_CHILD_AT_IMPORT = running_as_gate_child()


def resolve_frontend(node_id: str, repo_root: Path) -> bool:
    """True if a vitest test by this name exists in the named file.

    A vitest name cannot be imported from here, so this is the one place the
    registry falls back to looking for a literal. It checks that a proof
    EXISTS, never what it asserts.

    The literal is anchored on both sides. On the left by the opening `("` of
    the `it(...)` / `test(...)` call, which drops the prose mentions of a rule
    in comments (`// S-09: localStorage.setItem only in store files`). On the
    right by either the closing quote or the colon that static-safety.test.ts
    puts after a rule id (`it("S-11: no Authorization header in source")`).

    Requiring the closing quote alone was measured and rejected: it drops
    S-09, S-09b, S-11 and S-11b, whose registered names are deliberately
    PREFIXES of a longer title and never meet a closing quote. Requiring
    nothing on the right is what shipped, and it is why `"S-11` matched
    `"S-11b`.

    A name introduced by `it.skip` or `it.todo` is refused. Text matching
    cannot tell whether a vitest test passes, but it can tell that this one
    is not going to run at all, and a test nobody runs is the same non-proof
    a skipped pytest node is.
    """
    path_part, _, name = node_id.partition("::")
    path = repo_root / path_part
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    for closer in ('"', ':'):
        needle = f'("{name}{closer}'
        start = 0
        while (found := text.find(needle, start)) != -1:
            if _introduces_a_running_test(text, found):
                return True
            start = found + 1
    return False


#: Modifiers that mean the test is not going to run. `.only` and `.skipIf` are
#: deliberately absent: the first runs, and the second may.
_SWITCHED_OFF = (".skip", ".todo", ".fails")

#: Whole call names that never declare a running test. `describe` is here
#: because a registered node id names a TEST, and a suite title is not one: an
#: empty `describe` carrying the right title would otherwise read as a proof.
_NOT_A_TEST_CALL = ("xit", "xtest", "xdescribe", "describe", "suite")


def _introduces_a_running_test(text: str, paren: int) -> bool:
    """True when the `(` at this offset opens a call that declares a test.

    Two things have to hold. The parenthesis must belong to a CALL at all, so
    a title parked in a variable (`const banner = ("S-80: ...")`) is not
    mistaken for a test. And the call must be one vitest will actually run.

    The argument list in front of it is walked back over rather than looked
    past. `it.each([...])("name", ...)` puts a `)` immediately before the
    call parenthesis, and a rule that accepted `)` and stopped there let
    `it.skip.each([...])(`, `describe.each([...])(` and every other switched
    off table form through: none of them END in `.skip`, so the check for
    switched-off forms never ran at all. Measured against real vitest
    spellings, that one short circuit produced nineteen false positives.
    """
    head = _callee_before(text, paren)
    if not head:
        return False
    last = head.split("(")[-1].split(")")[-1]
    callee = last.strip().rsplit(" ", 1)[-1]
    # `it . skip (` is legal javascript. Spaces around the dots are removed
    # before the shape is judged, or the whole check is one space away from
    # being off.
    callee = "".join(callee.split())
    if not callee or not (callee[-1].isalnum() or callee[-1] in "_$"):
        return False
    if any(callee.endswith(form) for form in _SWITCHED_OFF):
        return False
    root = callee.split(".")[0]
    return root not in _NOT_A_TEST_CALL


def _callee_before(text: str, paren: int) -> str:
    """The expression being called, with any preceding argument list removed.

    Returns "" when the parenthesis does not follow a callable expression at
    all, which is the `const banner = ("...")` case.
    """
    cursor = paren
    # Walk back over `(...)` argument lists, however many are chained.
    while cursor > 0 and text[cursor - 1] in ") \t\n":
        while cursor > 0 and text[cursor - 1] in " \t\n":
            cursor -= 1
        if cursor == 0 or text[cursor - 1] != ")":
            break
        depth = 0
        while cursor > 0:
            cursor -= 1
            if text[cursor] == ")":
                depth += 1
            elif text[cursor] == "(":
                depth -= 1
                if depth == 0:
                    break
        if depth != 0:
            return ""
    start = max(0, cursor - 48)
    return text[start:cursor]


def _normalise(node_id: str) -> str:
    """One spelling for one node id.

    pytest prints `tests/test_x.py::test_y` and matching against its output is
    how collectability is decided, but a registry entry is typed by hand.
    `tests\\test_x.py` is the natural Windows spelling, `./tests/test_x.py`
    comes out of a shell, and `Path.is_file()` accepts both - so all three
    reached the run step, matched nothing pytest printed, and were rewarded
    with a RuntimeError that named the whole registry as unmeasurable. They
    are the same node id and are written the same way here.
    """
    file_part, sep, rest = node_id.partition("::")
    file_part = file_part.replace("\\", "/")
    while "//" in file_part:
        file_part = file_part.replace("//", "/")
    while file_part.startswith("./"):
        file_part = file_part[2:]
    return file_part + sep + rest


def resolve_backend(
    node_id: str,
    backend_root: Path,
    prime: Callable[[], Iterable[str]] | None = None,
) -> bool:
    """True if pytest can collect this node id AND it passes today.

    `prime` names the whole registry this call belongs to. When the requested
    node is one of them the entire set is measured in a single child pytest,
    which turns a hundred and fifty interpreter starts into one.
    """
    node_id = _normalise(node_id)
    key = (str(backend_root), node_id)
    if key in _VERDICTS:
        return _VERDICTS[key]

    batch = {node_id}
    if prime is not None:
        registry = {_normalise(n) for n in prime()
                    if not n.startswith("frontend/")}
        if node_id in registry:
            batch |= registry

    _measure(backend_root, batch)
    return _VERDICTS.get(key, False)


def _measure(backend_root: Path, node_ids: set[str]) -> None:
    """Fill the cache for every node id in the batch."""
    if running_as_gate_child():
        raise RuntimeError(
            "the contract gate tried to start a child pytest from inside one "
            "of its own children; a registered proof points back at a "
            "contract file and the recursion guard stopped it"
        )

    collectible = _collect(backend_root, node_ids)
    for node_id in node_ids:
        if node_id not in collectible:
            _VERDICTS[(str(backend_root), node_id)] = False

    green = _run(backend_root, collectible)
    for node_id in collectible:
        _VERDICTS[(str(backend_root), node_id)] = node_id in green


def _collect(backend_root: Path, node_ids: set[str]) -> set[str]:
    """The subset pytest actually collects, measured by --collect-only."""
    files = sorted({
        node_id.partition("::")[0] for node_id in node_ids
        if (backend_root / node_id.partition("::")[0]).is_file()
    })
    if not files:
        return set()

    root_key = str(backend_root)
    surveyed = _SURVEYED.setdefault(root_key, set())
    collected = _COLLECTED.setdefault(root_key, set())

    fresh = [f for f in files if f not in surveyed]
    for chunk in _chunks(fresh):
        completed = _pytest(
            backend_root,
            ["--collect-only", "-q", "--continue-on-collection-errors", *chunk],
        )
        found = 0
        for line in completed.stdout.splitlines():
            stripped = line.strip()
            if "::" in stripped and stripped.startswith(tuple(chunk)):
                collected.add(stripped)
                found += 1
        # Exit 5 is "no tests collected" and exit 1 is "a module in here does
        # not import": both are real answers about the registry, and the node
        # ids they cover are correctly reported missing. Exit 0 with nothing
        # matched is not an answer - pytest collected something and spelled
        # it differently, which is what a moved rootdir looks like. Reporting
        # that as "no proofs found" would drop a whole registry on one
        # configuration change.
        if found == 0 and completed.returncode not in (1, 5):
            raise RuntimeError(
                "the contract gate collected nothing it recognised from "
                f"files that exist: {chunk}. pytest exited "
                f"{completed.returncode}.\n{completed.stdout[-2000:]}\n"
                f"{completed.stderr[-2000:]}"
            )
        surveyed.update(chunk)

    return {
        node_id for node_id in node_ids
        if node_id in collected
        or any(c.startswith(node_id + "[") for c in collected)
    }


def _run(backend_root: Path, node_ids: set[str]) -> set[str]:
    """The subset that passes. Skips and errors are not passes."""
    if not node_ids:
        return set()

    outcomes: dict[str, bool] = {}
    ordered = sorted(node_ids)
    with tempfile.TemporaryDirectory() as tmp:
        for index, chunk in enumerate(_chunks(ordered)):
            report = Path(tmp) / f"gate-{index}.xml"
            completed = _pytest(
                backend_root,
                ["-q", "--no-header", "-p", "no:randomly",
                 # An xfail that passes is not evidence: its own author
                 # declared it broken. Strict turns that into a failure so it
                 # reads as one here too.
                 "-o", "xfail_strict=true",
                 f"--junitxml={report}", *chunk],
            )
            # 0 is all green and 1 is some red; both are answers about the
            # tests. Anything else means the run did not happen the way it was
            # asked to, and reporting that as "none of these proofs exist"
            # would call a whole registry broken because of one interrupted
            # process.
            #
            # Three separate ways it can go wrong, and the first draft checked
            # only the first: no report at all, a report that is missing node
            # ids the run was given, and an exit code that is neither of the
            # two answers. An interrupted pytest DOES write a junit file, so
            # the file's existence proves nothing on its own - measured, with
            # a chunk of a hundred and twenty-eight proofs quietly reported as
            # missing.
            if not report.is_file():
                raise _did_not_run(
                    "wrote no report", completed, chunk)
            if completed.returncode not in (0, 1):
                raise _did_not_run(
                    f"exited {completed.returncode}, which is neither all "
                    "green nor some red", completed, chunk)
            seen: set[str] = set()
            for case in ET.parse(report).iter("testcase"):
                name = case.get("name") or ""
                classname = case.get("classname") or ""
                passed = not any(
                    child.tag in ("failure", "error", "skipped")
                    for child in case
                )
                for node_id in chunk:
                    if _case_belongs_to(classname, name, node_id):
                        seen.add(node_id)
                        # AND, not OR. A parametrised proof is green only when
                        # every one of its cases is: one red parameter makes
                        # the claim false, and reading "any of them passed" as
                        # evidence is how a half-broken guarantee reads as
                        # proven.
                        outcomes[node_id] = outcomes.get(node_id, True) and passed
            missing = [node_id for node_id in chunk if node_id not in seen]
            if missing:
                raise _did_not_run(
                    f"reported nothing for {len(missing)} of the {len(chunk)} "
                    f"node ids it was given, starting with {missing[0]!r}",
                    completed, chunk)
    return {node_id for node_id, passed in outcomes.items() if passed}


def _did_not_run(what: str, completed: subprocess.CompletedProcess[str],
                 chunk: list[str]) -> RuntimeError:
    """The one error for "the measurement did not happen".

    Held apart so every caller says the same thing, and so the difference
    between this and a verdict stays visible at the call site: a verdict is
    about the tests, this is about the run.
    """
    return RuntimeError(
        f"the contract gate's child pytest {what}, over {len(chunk)} node "
        "ids. This is an infrastructure fault, not a missing proof.\n"
        f"{completed.stdout[-2000:]}\n{completed.stderr[-2000:]}"
    )


def _case_belongs_to(classname: str, name: str, node_id: str) -> bool:
    """Map a junit testcase back onto the node id that selected it.

    junit records `tests.test_x.TestY` / `test_z[param]` where the node id
    says `tests/test_x.py::TestY::test_z`. Parametrised cases carry a suffix
    the registry never spells out, so the leaf is matched by equality or by
    an opening bracket, never by bare prefix - `test_a` must not swallow
    `test_ab`.
    """
    file_part, _, rest = node_id.partition("::")
    parts = [p for p in rest.split("::") if p]
    if not parts:
        return False
    module = file_part.removesuffix(".py").replace("/", ".")
    expected_class = ".".join([module, *parts[:-1]])
    leaf = parts[-1]
    return classname == expected_class and (
        name == leaf or name.startswith(leaf + "[")
    )


def _chunks(items: list[str]) -> list[list[str]]:
    chunks: list[list[str]] = [[]]
    size = 0
    for item in items:
        if chunks[-1] and size + len(item) > _MAX_COMMAND_CHARS:
            chunks.append([])
            size = 0
        chunks[-1].append(item)
        size += len(item) + 1
    return [chunk for chunk in chunks if chunk]


def _pytest(backend_root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env[DEPTH_ENV] = "1"
    # The child's verdict has to be about the test, not about whatever the
    # developer's shell happens to be exporting. PYTEST_ADDOPTS can add -x or
    # -k and change what runs; PYTEST_PLUGINS can load something that changes
    # how it runs; COVERAGE_FILE would send the child's writes at a path this
    # process does not own. PYTEST_CURRENT_TEST is the parent's own bookkeeping
    # and means nothing one level down.
    for leaked in ("PYTEST_CURRENT_TEST", "PYTEST_ADDOPTS", "PYTEST_PLUGINS",
                   "COVERAGE_FILE", "COVERAGE_PROCESS_START"):
        env.pop(leaked, None)
    try:
        return subprocess.run(
            # --rootdir is pinned, not inferred. Node ids are spelled
            # relative to it, so the day a pyproject.toml lands at the
            # repository root every registered proof would stop matching.
            [sys.executable, "-m", "pytest", "-p", "no:cacheprovider",
             f"--rootdir={backend_root}", *args],
            cwd=str(backend_root),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_CHILD_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as expired:
        # Loud, not quiet. Without this the parent waits forever on a hung
        # registered proof and the suite produces no output at all, which
        # looks like a slow machine rather than a stuck test.
        raise RuntimeError(
            "the contract gate's child pytest did not finish within "
            f"{_CHILD_TIMEOUT_SECONDS}s; one of the registered proofs hangs"
        ) from expired
