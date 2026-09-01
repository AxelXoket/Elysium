"""The house rule enforced over the SUITE itself: a test that reads source
text to make an assertion is banned.

The reason is exact, and it is the owner's own: a test that greps a
function's source passes when the line is present but dead, present but in an
unreachable branch, present but commented out inside a string, or when the
behaviour has moved somewhere the grep no longer looks. It reports coverage it
does not have. Three of these were found in this suite - one written the very
week the rule was last restated - and fixed behaviourally:

  * test_notebook_crud.py::test_the_migration_source_connection_keeps_scratch_in_ram
  * test_release_hardening.py::TestPerMonitorDpi::test_it_runs_before_the_window_is_created
  * test_release_hardening.py::TestAuditRegressions2026_07_25::test_a_refused_load_does_not_orphan_the_resident_model

A fourth turned up while this file was being built, in the same file as the
second and third above, and was fixed the same way:

  * test_release_hardening.py::test_run_app_reports_the_override_after_installing_its_handler

Nothing stopped a fifth from appearing anywhere else in tests/. This file is
that stop.

WHAT COUNTS, AND THE TWO CONFIDENCE LEVELS THIS FILE ACTS ON
--------------------------------------------------------------
Reflection calls that hand back a function's own body as a string - inspect's
getsource, getsourcelines and getsourcefile - and reading the constant pool
off a code object (its co_consts table) are unambiguous: nothing legitimate in
this suite has ever needed either, confirmed by a plain grep across tests/ and
verify/ on 2026-08-19. Both are hard-gated across the WHOLE tests/ tree below,
with no exceptions and no allowlist entry to argue for, because there is
nothing to argue.

Reading a .py file's text and asserting a substring is in it, or comparing two
`.index()` positions from that text, is not unambiguous. tts/worker/*.py and
routers/tts_runtime.py are exercised by tests in a FOREIGN interpreter this
suite does not control (see test_lint_gates.py's own docstring on why), so
some of the files that read a module's text are working around a real
constraint, not dodging one - and telling that apart from a lazy grep, in a
file outside this pass's ownership, needs the kind of judgement an automated
sweep cannot supply on its own. Calibrating this rule against the real tree on
2026-08-19 turned up matches in several tts_* test modules outside this pass's
three owned files (test_notebook_crud.py, test_release_hardening.py, this
file) - and one near-match, in test_worker_dsp.py, that reads a worker script
only to build a deliberately broken COPY it then runs a real check against:
the text there is an ingredient in something that EXECUTES, never the subject
of an assertion, which is exactly the distinction this whole file exists to
draw.

So that rule is hard-gated on this pass's own three files - a fifth violation
there fails the build the run it lands in - and TRACKED, not enforced,
everywhere else: what the sweep already found is pinned by exact text below,
the same way verify_hygiene.py pins a waiver. A pinned hit that no longer
matches means somebody already fixed it and the entry is stale; a hit found
ANYWHERE ELSE in the tree that is not pinned is a genuine new one and fails
the build. The pinned list is not permission to add another one - it is the
one this pass found and could not fix without touching files outside its
scope, written down rather than hidden.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

# Built at runtime, never typed here as contiguous text. This file's whole job
# is to notice these exact shapes in OTHER files, which means its own samples
# have to spell them out somewhere - and typing them directly would make this
# file trip its own rules on itself, the same trap a literal em dash in a
# dash-forbidding regex was for settings-copy.test.ts (see
# test_hygiene_gate.py's module docstring). Assembled from pieces instead, so
# the finished shape exists only in memory once the module has been imported.
_GETSOURCE = "inspect" + "." + "getsource"
_GETSOURCELINES = "inspect" + "." + "getsourcelines"
_GETSOURCEFILE = "inspect" + "." + "getsourcefile"
_CO_CONSTS = "." + "co_consts"
_PY = "." + "py"  # the extension this scanner looks for next to a read call

# ── The three rules ──────────────────────────────────────────────────────────

_INSPECT_RE = re.compile(
    r"inspect\s*\.\s*(getsource|getsourcelines|getsourcefile)\s*\(")
# Built in two pieces, like the constants above: typed as one literal regex
# this line would itself end in the exact dotted attribute name it looks
# for - the same self-reference this whole file is careful about everywhere
# else.
_CO_CONSTS_RE = re.compile(r"\." + r"co_consts\b")

# A read_text()/.read() call is a candidate only when a quoted path ending in
# the scanned extension sits within a few lines above it - the shape every
# real hit shares, whether the path is built in one line or wrapped in
# parentheses across several - and it is waved through when that same window
# already feeds ast.parse(: a structural claim about a foreign-venv script,
# which is the legitimate use this repo has for reading a module's text at
# all (see test_privacy_source_boundaries.py and test_privacy_promises.py).
_PY_PATH_RE = re.compile(r"""["'][^"'\n]*\.py["']""")
_READ_CALL_RE = re.compile(r"\.read_text\s*\(|\.read\s*\(\s*\)")
_ASSERT_IN_RE = re.compile(r"\bassert\b.*\bin\b")
_AST_PARSE_RE = re.compile(r"\bast\s*\.\s*parse\s*\(")
_WINDOW = 5  # lines of look-back for a path literal near a read call


@dataclass(frozen=True)
class Hit:
    path: str
    rule: str
    line: str  # exact text of the offending line, stripped - the anchor


def scan_file(path: str, text: str) -> list[Hit]:
    """Every hit in one (path, text) pair. Pure - no disk, no git - so the
    tests below can hand it synthetic content and mean it."""
    hits: list[Hit] = []
    if _INSPECT_RE.search(text):
        hits.append(Hit(path, "S-01", "reflection call reading a function's own body"))
    if _CO_CONSTS_RE.search(text):
        hits.append(Hit(path, "S-02", "code object constant pool sniffed"))

    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not _READ_CALL_RE.search(line):
            continue
        window = "\n".join(lines[max(0, i - _WINDOW):i + 1])
        if (_PY_PATH_RE.search(window) and not _AST_PARSE_RE.search(window)
                and _ASSERT_IN_RE.search(text)):
            hits.append(Hit(path, "S-03", line.strip()))
    return hits


# ── The allowlist: legitimate whole-tree scanners ───────────────────────────
#
# Each of these reads MANY files, or reads one file to answer a SHAPE question
# with no behaviour behind it to observe instead - the exception
# test_tree_hygiene.py names in its own docstring, applied here to the same
# class of file. None of them currently trip scan_file() (the window
# correlation above already tells a whole-tree sweep apart from a single-file
# behaviour substitute), but the allowlist exists anyway, on the owner's
# explicit instruction, as the argued-for exemption a future tightening of the
# read-and-assert rule would need to cross.
ALLOWLIST: dict[str, str] = {
    "test_egress_chokepoint" + _PY:
        "scans the shipped app for a second network-client builder outside "
        "the one egress chokepoint - a SHAPE claim, not one module's "
        "behaviour.",
    "test_tree_hygiene" + _PY:
        "loads verify_hygiene.py and sweeps the real working tree for "
        "hygiene violations; the text IS the subject, per its own docstring.",
    "test_lint_gates" + _PY:
        "runs ruff over the worker directory and checks the configured scope "
        "still matches real files on disk - a config/scope claim.",
    "test_privacy_source_boundaries" + _PY:
        "counts how many modules build image_url / set stream:true across "
        "the whole backend - a SHAPE claim with no behaviour to run instead.",
    "test_privacy_promises" + _PY:
        "AST-walks every backend module for outbound-call sites to enumerate "
        "who can reach the network - a whole-tree census.",
    "test_log_identifier_privacy" + _PY:
        "AST-walks every shipped backend module for a logging call that can "
        "carry vault content or an on-screen name - the same whole-tree "
        "census as test_privacy_promises, for the other plaintext exit "
        "(elysium.log). Its own docstring argues the exception; its scanner "
        "lives in log_leak_scan.py and parses rather than string-matches.",
    "test_release_tree" + _PY:
        "builds a real git archive and inspects what it contains - a claim "
        "about what git publishes, not about any module's behaviour.",
    "test_release_sync" + _PY:
        "compares PyInstaller specs and docs against the backend directory "
        "listing - a structural sync claim across many files.",
    "test_artifact_gate" + _PY:
        "inspects the real packaged exe's contents against the source tree; "
        "the one test that has to look at a real build output.",
}

# ── This pass's own files: hard gate, no exceptions ─────────────────────────
OWNED_FILES = frozenset({
    "test_notebook_crud" + _PY,
    "test_release_hardening" + _PY,
    "test_tests_are_behavioural" + _PY,
})

# ── Everywhere else: tracked, not enforced ──────────────────────────────────
#
# Compiled by hand from a scan_file() pass over the real tree on 2026-08-19,
# after excluding test_worker_dsp.py (the near-match described in the module
# docstring: it reads a worker script to build a deliberately broken COPY it
# then runs a real check against, never to assert on the text itself). Every
# remaining entry is the same shape as the four this pass fixed: a read_text()
# result, string-matched or index()-compared, standing in for behaviour. None
# of these files are this pass's to edit - it owns exactly the three named in
# OWNED_FILES above.
#
# The extension is held apart from the rest of each line, the same way it is
# everywhere else in this file: a registry whose whole job is to record "a
# quoted .py path sits next to a read call here" would otherwise BE one of
# those, on itself, the moment it spells the pinned line out in full.
#
# The anchor is the exact stripped text of the read_text()/.read() line. If
# that line changes - fixed, moved, reworded - the entry stops matching and
# the sweep test below fails, the same self-cleaning property
# verify_hygiene.py's waiver list has: this list may only ever be argued
# smaller, never quietly left stale.
KNOWN_PENDING_S03: dict[str, tuple[str, ...]] = {
    # PAID, 1 September 2026. test_tts_host.py had three reads of tts/host.py.
    # Two were tests: one sliced the file between two landmarks and asserted
    # "self._uid = model.uid" sat inside the slice, the other asserted
    # "self._uid = prior_uid" appeared anywhere at all. The third was a dead
    # `_host_source()` helper nobody called. All three are gone. The pair now
    # drives the host: one parks a real load inside check_fit and reads the
    # snapshot from another thread (what /tts/active sees while the card
    # fills), the other loads a model, shrinks free VRAM under it and checks
    # the resident identity survived the refusal - with a positive control
    # proving a refusal on an EMPTY host still claims nothing.
    # PAID, KADEME S03. test_tts_lock_lifecycle.py's SOURCES/_src helper is
    # gone with both of its callers. One asserted the STRING
    # "TTS_IDLE_UNLOAD_S" was absent from config.py - which the same reaper
    # passes under any other name, or hard-coded, or off an env overlay the
    # file never mentions. The other asserted two COMMENT blocks still said
    # "idle unload"; comments do not run, and it was deleted rather than
    # converted. What stands in their place drives the pulse: a scan of the
    # live config namespace (with its own positive control), and a sweep that
    # sets every numeric TTS_* setting to 0 and to 1 in turn and asserts a
    # day-idle model is still loaded and its worker still open.
    #
    # PAID, 31 August 2026. Two tests here sliced `tts_runtime.py`'s own text
    # between two `def` lines and compared substring positions - so they
    # asserted that `speaker.cancel()` appears before `speaker.close` in the
    # FILE, which is equally true of a `finally` that never runs. Both drive
    # the endpoint now: one records the THREAD each teardown call arrives on,
    # the other abandons the stream mid-utterance and waits for the close.
    #
    # PAID, 1 September 2026. test_tts_vram_cost.py had two reads of
    # tts/worker/fish_s2.py. One sliced `_build_model` out of the file between
    # two `def` lines and looked for the string `STATE["model_parked"] = None`
    # inside it - true of a line sitting in a branch nothing reaches, false of
    # the identical rule spelled any other way. The other compared two
    # SUBSTRING POSITIONS inside `_free_for_codec`, asserting `_park_model`
    # appears earlier in the FILE than `STATE["model"] = None`; text order is
    # not execution order. Both run the real functions now against a fake
    # engine: one builds a model with a stale park in the slot and checks the
    # slot afterwards, the other evicts a resident model and checks where the
    # model ENDED UP - parked, restored without a rebuild - with a positive
    # control proving an empty park and a real rebuild are both reachable.
    # PAID, 1 September 2026. test_tts_worker.py's three reads are gone.
    # Two of them sliced fish_s2.py's text - for the sticky compile_broken
    # flag, the compile decision in _build_model, and the codec prewarm's
    # position relative to the model_path publication, that last one by
    # comparing two str.index() results. The third grepped worker_client.py
    # for the subtraction that makes the load timeout a SILENCE budget. All
    # of it runs now: the load path drives the real fish_s2 module against a
    # faked engine boundary (the same trade fish_synth_harness.py makes), and
    # the silence budget is measured against a real subprocess over real
    # pipes that reports for three seconds against a one-second budget.
    #
    # The one read LEFT in that file is not pinned here and is not a hit:
    # test_every_note_our_workers_send_is_in_the_vocabulary feeds the text to
    # ast.parse() and resolves each note against the IMPORTED _wire module, so
    # scan_file() waves it through under the same rule as
    # test_privacy_promises.py.
}

# One file's read_text() is NOT pinned above, on purpose: test_worker_dsp.py
# matches the same shape scan_file() looks for (see
# TestTheDetectorDoesNotFireOnTheInnocentLine::
# test_reading_a_py_file_to_build_something_that_runs_is_not_flagged_by_intent
# for the synthetic proof), but by hand it reads _dsp.py only to build a
# deliberately broken COPY it then runs a real check against - the text is an
# ingredient in something that executes, never the subject of an assertion.
# Excluded here rather than pinned as a violation, because it is not one; see
# the module docstring for the same note in prose.
#: Files whose source reads are an INGREDIENT, never the subject.
#:
#: `test_worker_dsp.py` reads `_dsp.py` and appends `_TAIL_BUG` to it, then
#: RUNS the broken copy in a real interpreter to prove the check it is about
#: actually fires. The text is fed to something that executes; no assertion
#: is made about the text itself.
#:
#: THE REASON WAS WIDER THAN THE TRUTH until 31 August 2026. The same file
#: also asserted `f"MIN_RATE = {speed.MIN_RATE}" in src` - a pure text claim,
#: and a weak one: `0.80` formats as `0.8`, so `MIN_RATE = 0.85` in the
#: worker file contains the needle and the one divergence that test exists to
#: catch went straight through it. That test parses and compares the VALUE
#: now. The exemption stays for the two reads it was actually written for.
_NOT_A_VIOLATION = frozenset({"test_worker_dsp" + _PY})


def _sweep() -> list[Hit]:
    glob_pattern = "*" + _PY
    hits: list[Hit] = []
    for path in sorted(TESTS_DIR.glob(glob_pattern)):
        if path.name in ALLOWLIST or path.name in _NOT_A_VIOLATION:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        hits.extend(scan_file(path.name, text))
    return hits


# ── Proof the detector can fire at all ──────────────────────────────────────
#
# Same discipline test_hygiene_gate.py uses for the tree-wide hygiene gate: a
# gate with no proof it can fail is the exact defect class this file exists
# to close. Every sample below is synthetic - nothing here touches the real
# tree - so these stay meaningful on a machine whose tests/ happens to be
# mid-edit.


class TestTheDetectorFires:
    def test_inspect_getsource_is_a_hit(self) -> None:
        hits = scan_file("sample", f"src = {_GETSOURCE}(thing.load)\n")
        assert [h.rule for h in hits] == ["S-01"]

    def test_getsourcelines_is_a_hit_too(self) -> None:
        hits = scan_file("sample", f"{_GETSOURCELINES}(thing.load)\n")
        assert [h.rule for h in hits] == ["S-01"]

    def test_getsourcefile_is_a_hit_too(self) -> None:
        hits = scan_file("sample", f"{_GETSOURCEFILE}(thing)\n")
        assert [h.rule for h in hits] == ["S-01"]

    def test_co_consts_sniffing_is_a_hit(self) -> None:
        hits = scan_file("sample", f"assert 'x' in fn.__code__{_CO_CONSTS}\n")
        assert [h.rule for h in hits] == ["S-02"]

    def test_reading_a_py_file_and_asserting_on_it_is_a_hit(self) -> None:
        sample = (
            'src = (Path(__file__).parent / "thing' + _PY + '").read_text()\n'
            'assert "the_call()" in src\n'
        )
        hits = scan_file("sample", sample)
        assert [h.rule for h in hits] == ["S-03"]
        assert hits[0].line == (
            'src = (Path(__file__).parent / "thing' + _PY + '").read_text()')


class TestTheDetectorDoesNotFireOnTheInnocentLine:
    def test_a_plain_behavioural_test_is_not_a_hit(self) -> None:
        sample = (
            "def test_it_returns_the_value():\n"
            "    assert thing.load() == 'value'\n"
        )
        assert scan_file("sample", sample) == []

    def test_a_distant_py_mention_is_not_a_hit(self) -> None:
        """The false alarm test_privacy_contract.py and
        test_security_contract.py would have been, before the window
        correlation existed: an unrelated quoted module name sitting far from
        a README.md/SECURITY.md read_text() with no path relation between
        them."""
        far_apart = "\n".join(f"# padding line {n}" for n in range(_WINDOW + 2))
        sample = (
            'OTHER_FILES = ["tests/test_security_headers' + _PY + '"]\n'
            + far_apart + "\n"
            'text = README.read_text(encoding="utf-8")\n'
            'assert "Privacy" in text\n'
        )
        assert scan_file("sample", sample) == []

    def test_reading_a_py_file_to_build_something_that_runs_is_not_flagged_by_intent(
        self,
    ) -> None:
        """test_worker_dsp.py's actual shape: the text is copied into a file
        that is then EXECUTED, and the real check's exit code is what gets
        asserted on - never the text itself. This detector cannot yet tell
        that apart from a real hit by construction alone (recorded as the one
        excluded near-match in the module docstring and in the comment above
        KNOWN_PENDING_S03); recorded here as the honest negative result this
        heuristic does NOT produce, so that gap stays visible rather than
        silently patched over.
        """
        sample = (
            # Same order as the real file: the quoted path sits ABOVE the
            # read call it feeds, which is what puts it inside this
            # detector's look-back window in the first place.
            '(pkg / "_dsp' + _PY + '").write_text(\n'
            '    WORKER_DSP.read_text(encoding="utf-8") + TAIL, encoding="utf-8")\n'
            "result = subprocess.run([sys.executable, str(checker)])\n"
            'assert result.returncode != 0, "the check did not notice"\n'
            # An unrelated assert elsewhere in the same file, exactly like the
            # real one: test_worker_dsp.py's actual "in" comes from other
            # tests in the same module, not from this function.
            'assert "numpy" in str(checker)\n'
        )
        hits = scan_file("sample", sample)
        assert [h.rule for h in hits] == ["S-03"], (
            "if this now returns [], the heuristic grew a way to tell a "
            "read-to-execute from a read-to-assert and the docstring note "
            "above about test_worker_dsp.py needs to change too"
        )

    def test_ast_structural_reads_are_not_a_hit(self) -> None:
        """test_privacy_promises.py's and test_tts_packaging.py's shape: the
        text feeds ast.parse(), and everything downstream inspects the PARSED
        TREE, not the text - the legitimate route for a script this suite
        cannot import (see test_lint_gates.py's docstring on tts/worker/)."""
        sample = (
            'tree = ast.parse(script.read_text(encoding="utf-8"))\n'
            "names = [n.func.attr for n in ast.walk(tree) "
            "if isinstance(n, ast.Call)]\n"
            'assert "close" in names\n'
        )
        assert scan_file("sample", sample) == []


# ── The real tree: hard where the detector is unambiguous ──────────────────


def test_no_file_in_tests_uses_reflection_to_read_its_own_source() -> None:
    """S-01, whole tree, no exceptions. Confirmed by a plain grep across
    tests/ and verify/ on 2026-08-19 that nothing legitimate needs it - and
    nothing could: the reflection call requires the object to be IMPORTABLE
    in this interpreter, which is exactly the property the tts/worker/
    foreign-venv scripts (the one real excuse for reading a module's text at
    all in this suite) do not have.
    """
    hits = [h for h in _sweep() if h.rule == "S-01"]
    assert hits == [], "\n".join(
        ["", "a reflection read of a function's own source was found:", ""]
        + [f"  {h.path}: {h.line}" for h in hits])


def test_no_file_sniffs_code_object_constants() -> None:
    hits = [h for h in _sweep() if h.rule == "S-02"]
    assert hits == [], "\n".join(
        ["", "code object constant pool sniffing was found:", ""]
        + [f"  {h.path}: {h.line}" for h in hits])


def test_this_passs_own_files_never_substitute_source_text_for_behaviour():
    """Hard, but only where this pass has the standing to fix a hit: its own
    three files. A fifth violation showing up in ANY of them fails here the
    moment it lands, same run it was written in."""
    hits = [h for h in _sweep() if h.rule == "S-03" and h.path in OWNED_FILES]
    assert hits == [], "\n".join(
        ["", "a source-text-substitute test reappeared in an owned file:", ""]
        + [f"  {h.path}: {h.line}" for h in hits]
        + ["", "Fix it behaviourally - see this file's module docstring for "
                "the four that were."])


def test_every_other_hit_is_the_one_this_pass_already_found() -> None:
    """Tracked, not enforced, for files outside OWNED_FILES - and tracked
    precisely: the set of (file, line) hits the real sweep finds must equal
    KNOWN_PENDING_S03 exactly, in both directions.

    A hit this sweep finds that is NOT pinned is a genuine new one - in a
    file nobody has excused - and fails here. A pinned entry the sweep no
    longer finds means the line changed (fixed, moved, reworded) and the
    entry is stale; that fails too, on purpose, so this list can only ever be
    argued smaller. It is not a way to let a sixth one in quietly: adding a
    new pin means editing this file and saying why in review, same as editing
    hygiene_allowlist.txt does for verify_hygiene.py.
    """
    found: dict[str, set[str]] = {}
    for hit in _sweep():
        if hit.rule == "S-03" and hit.path not in OWNED_FILES:
            found.setdefault(hit.path, set()).add(hit.line)

    pinned = {path: set(lines) for path, lines in KNOWN_PENDING_S03.items()}

    unexpected = {
        path: lines - pinned.get(path, set())
        for path, lines in found.items()
        if lines - pinned.get(path, set())
    }
    stale = {
        path: lines - found.get(path, set())
        for path, lines in pinned.items()
        if lines - found.get(path, set())
    }

    assert not unexpected, "\n".join(
        ["", "new source-text-substitute hit(s), not in KNOWN_PENDING_S03:", ""]
        + [f"  {p}: {ln}" for p, lns in unexpected.items() for ln in lns]
        + ["", "This is either a genuine new violation - fix it behaviourally "
                "- or KNOWN_PENDING_S03 needs a new, argued entry if it is "
                "truly out of reach this pass."])
    assert not stale, "\n".join(
        ["", "KNOWN_PENDING_S03 entries that no longer match anything:", ""]
        + [f"  {p}: {ln}" for p, lns in stale.items() for ln in lns]
        + ["", "Somebody fixed it. Remove the entry."])


def test_the_allowlist_has_nothing_dead_in_it() -> None:
    """Every ALLOWLIST name must exist under tests/, or it is excusing a file
    that is not there - dead weight nobody will ever revisit."""
    missing = [name for name in ALLOWLIST if not (TESTS_DIR / name).is_file()]
    assert missing == [], f"allowlisted but not present in tests/: {missing}"


def test_this_file_does_not_flag_itself() -> None:
    """The trap test_hygiene_gate.py's module docstring names: a scanner for
    a pattern that types the pattern in order to look for it can trip on its
    own source. Every sample above is assembled from pieces at import time
    rather than typed as one contiguous shape, for exactly this reason - this
    is the test that would catch it if one of them ever stopped being built
    that way.
    """
    hits = scan_file(Path(__file__).name,
                     Path(__file__).read_text(encoding="utf-8"))
    assert hits == [], hits
