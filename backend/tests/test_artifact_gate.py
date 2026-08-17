"""test_artifact_gate.py - the one test that looks at a real build output.

KADEME 21b, corrected in K-37. Every other test in this repo reads source.
Nothing ever opened the thing the user actually double-clicks, so the spec's
`datas` list could drop `frontend_dist` and the whole suite would stay green on
an exe that serves a blank page - or one that cannot speak, because the worker
scripts and the pinned requirements are plain data files PyInstaller omits
unless the spec says otherwise.

This lives in pytest on purpose. KADEME 07 measured the cost of the other
arrangement: sixteen scripts under backend/verify/, nobody running them, three
already dead. A gate that needs somebody to remember it is not a gate.

Two halves:

  * The classifying and staleness logic is pure - BOTH sides of every
    comparison are arguments - so it is proven with synthetic input whether or
    not an exe exists.
  * The assertions about the real artefact skip, loudly, when there is no exe
    to look at. Skipping is right here: a missing build is not a broken build,
    and a suite that is red on every clean checkout gets ignored.

NOTHING HERE SPELLS OUT WHAT THE BUILD SHOULD CONTAIN. K-37 measured what
happened when it did: the old `REQUIRED` tuple named exactly one file under
`frontend_dist/`, so a build shipping `index.html` without the JS bundle - a
blank page - passed the gate that exists to catch that. run_app's own selftest
misses it too; it only looks for `id="root"` in the document. The payload is
now derived on BOTH sides. The disk side applies the spec's own rules
(elysium_onefile.spec:24 whole `frontend/dist`, :35-39 `*.py` minus
`__init__.py`, :40 whole `tts/requirements`); the archive side is read out of
the exe. A seventh engine, or a second JS chunk, is covered the day it lands.

STALENESS compares two COMMIT times. Measured the hard way in KADEME 21a: a
`git checkout` that restores a file to its committed bytes still moves its
mtime, so an mtime comparison reported production code as "changed" when
nothing about it had changed. Commit time answers the question actually being
asked - did the CONTENT move. K-37 found the exe half of that sentence was
never true (see `exe_epoch`).

WHAT THIS GATE CANNOT DO, written down so a green run is not read as more than
it is:

  * It cannot prove the exe was built from the FINAL bytes of its commit. The
    exe is committed alongside the source it was built from, so both carry the
    same stamp, and building -> editing a source file -> committing both is
    invisible here. Only a manifest of input hashes baked into the exe could
    answer that.
  * `frontend/dist` is a build output and untracked, so the frontend payload
    can only be compared set-for-set when a dist happens to be on disk. A
    clean clone has none; there the check degrades to "the document, at least
    one script, at least one stylesheet" - enough to catch a blank page, not
    enough to catch one missing chunk of several. `expected_payload` says which
    half it could not decide, and the caller asserts the degraded form rather
    than treating an empty expectation as satisfied.
  * The FRONTEND half of the staleness pathspec is still spelled out here,
    because the artefact carries one content-hashed bundle and cannot say which
    sources produced it. Only the backend exclusions are checked against the
    archive, and only in the weak direction - see `NOT_AN_INPUT`.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent

#: The one-file build the repository ships. The spec writes backend/dist and a
#: copy is committed at the root; the root copy is what a downloader runs.
EXE_CANDIDATES = (REPO / "Elysium.exe", REPO / "backend" / "dist" / "Elysium.exe")

#: Tracked backend files whose contents cannot change what the exe contains, so
#: touching one does not make the build stale. K-37 measured the cost of not
#: having this list: `backend/ruff.toml` is a lint config that is never
#: packaged, and editing it demanded a 33 MB rebuild.
#:
#: The test below proves each entry is ABSENT from the archive. That is
#: necessary but not sufficient and the asymmetry is deliberate:
#: `requirements.lock.txt` is absent from the archive too, and is NOT excluded,
#: because changing a pin changes the packages that get frozen in. Absence
#: keeps the list from drifting into an excuse if a future spec starts
#: packaging one of these; it does not by itself justify an entry.
#:
#: `elysium_onefile.spec` is here because stale_reasons measures it separately,
#: under its own label - the old pathspec counted it twice.
NOT_AN_INPUT = (
    "backend/ruff.toml",
    "backend/requirements-dev.txt",
    "backend/elysium.spec",
    "backend/elysium_onefile.spec",
)

#: One definition, read by both stale_reasons and its positive control. Spelled
#: out for the frontend because the artefact cannot name its own sources.
BACKEND_INPUTS = [
    "backend",
    ":(exclude)backend/tests",
    ":(exclude)backend/verify",
    *(":(exclude)" + p for p in NOT_AN_INPUT),
]
FRONTEND_INPUTS = [
    "frontend/src",
    ":(exclude)frontend/src/test",
    "frontend/package.json",
    "frontend/vite.config.ts",
    "frontend/index.html",
]
SPEC_INPUT = ["backend/elysium_onefile.spec"]

#: Absent, or the build shipped somebody's data.
FORBIDDEN_NAMES = ("app.db", ".env", "salt.bin", "verifier.bin")
FORBIDDEN_SUFFIX = (".log",)


def _norm(name: str) -> str:
    return name.replace("\\", "/")


def expected_payload() -> tuple[dict[str, set[str]], list[str]]:
    """What the archive must carry, derived from disk by the spec's own rules.

    Returns `(expected, undecidable)`. `undecidable` names the payloads whose
    disk side is absent, so no expectation could be formed. The caller must
    degrade loudly instead of treating an empty expectation as satisfied - a
    silent empty set is the vacuous pass this whole file exists to prevent, and
    both earlier versions of this gate had one.
    """
    expected: dict[str, set[str]] = {}
    undecidable: list[str] = []

    # elysium_onefile.spec:24 - the whole directory travels.
    dist = REPO / "frontend" / "dist"
    found = {
        "frontend_dist/" + p.relative_to(dist).as_posix()
        for p in dist.rglob("*") if p.is_file()
    } if dist.is_dir() else set()
    if found:
        expected["frontend_dist"] = found
    else:
        undecidable.append("frontend_dist")

    # elysium_onefile.spec:35-39 - every *.py except the package marker.
    worker = REPO / "backend" / "tts" / "worker"
    found = {
        "tts_worker/" + p.name for p in worker.glob("*.py")
        if p.name != "__init__.py"
    }
    if found:
        expected["tts_worker"] = found
    else:
        undecidable.append("tts_worker")

    # elysium_onefile.spec:40 - the whole directory travels.
    reqs = REPO / "backend" / "tts" / "requirements"
    found = {"tts/requirements/" + p.name for p in reqs.glob("*.txt")}
    if found:
        expected["tts/requirements"] = found
    else:
        undecidable.append("tts/requirements")

    return expected, undecidable


def payload_gaps(expected: dict[str, set[str]], names) -> list[str]:
    """Every difference between what must be in the archive and what is.

    Both sides are arguments. The version K-37 replaced read its expectation
    from a module constant and was handed that same constant by its own
    positive control, so it passed for every value the constant could hold -
    the empty tuple included. A control that feeds a function its own source of
    truth measures nothing.

    Extra entries are reported as well as missing ones: an archive carrying a
    `frontend_dist/assets/index-OLD.js` the disk no longer has is a dist that
    moved after packaging, which is the same defect from the other side.
    """
    have = {_norm(n) for n in names}
    out = []
    for prefix, want in sorted(expected.items()):
        got = {n for n in have if n.startswith(prefix + "/")}
        out += ["missing: " + n for n in sorted(want - got)]
        out += ["unexpected: " + n for n in sorted(got - want)]
    return out


def blank_page_gaps(names) -> list[str]:
    """The degraded frontend check for when no dist is on disk to compare with.

    Not a substitute for the set comparison - it cannot see one missing chunk
    among several. It catches the failure that matters most and costs nothing to
    check: a build with no document, or no script, or no stylesheet, serves a
    blank page.
    """
    have = {_norm(n) for n in names}
    assets = {n for n in have if n.startswith("frontend_dist/")}
    out = []
    if "frontend_dist/index.html" not in assets:
        out.append("missing: frontend_dist/index.html")
    if not any(n.endswith(".js") for n in assets):
        out.append("missing: any frontend_dist script")
    if not any(n.endswith(".css") for n in assets):
        out.append("missing: any frontend_dist stylesheet")
    return out


def forbidden_in(names) -> list[str]:
    """Entries the archive must never carry."""
    out = []
    for n in names:
        tail = _norm(n).rsplit("/", 1)[-1].lower()
        if tail in FORBIDDEN_NAMES or tail.endswith(FORBIDDEN_SUFFIX):
            out.append(n)
    return out


def _git_epoch_or_none(pathspec: list[str]) -> int | None:
    done = subprocess.run(
        ["git", "log", "-1", "--format=%ct", "--", *pathspec],
        cwd=REPO, capture_output=True, text=True,
    )
    out = done.stdout.strip()
    return int(out) if out.isdigit() else None


def _git_epoch(pathspec: list[str]) -> int:
    """Commit time of the newest commit touching `pathspec`.

    Raises rather than returning 0. A silent 0 reads as "older than any exe",
    which the caller then treats as FRESH - so a mistyped pathspec, a repo
    with no commits, or a shallow clone would all report a healthy build
    while having measured nothing. That is the vacuous pass this whole file
    exists to prevent, and the first version of this function had it.
    """
    when = _git_epoch_or_none(pathspec)
    if when is None:
        raise AssertionError(
            "no commit found for %r. The staleness check cannot answer, so it "
            "must not answer 'fresh'." % (pathspec,)
        )
    return when


def exe_epoch(exe: Path) -> tuple[float, str]:
    """When the build entered the repository, and the clock that says so.

    A COMMITTED exe is measured by its COMMIT time. K-37 measured why. The exe
    is built before the commit that carries it, and it lands in that same
    commit, so its mtime is always EARLIER than the stamp on the source it was
    built from - by 390 seconds in the commit that exposed this. Comparing
    mtime against commit time therefore reported a stale build after every
    clean commit, permanently, which is precisely the state this module's own
    docstring calls a gate nobody runs.

    Commit time fixes the opposite failure too. In a fresh clone every file
    gets checkout mtimes, so an mtime baseline sits NEWER than every input and
    the reader can never find anything - blind in exactly the scenario this
    gate guards, somebody downloading the repository and running the exe. A
    commit stamp is the same in every clone.

    An UNTRACKED exe - a local build sitting in backend/dist that was never
    committed - has no commit stamp, so mtime is all there is. The label says
    which clock was used and the assertion prints it, because the two are not
    comparable and a reader deserves to know which one produced the verdict.
    """
    when = _git_epoch_or_none([exe.relative_to(REPO).as_posix()])
    if when is not None:
        return float(when), "commit"
    return exe.stat().st_mtime, "mtime"


def stale_reasons(base_epoch: float) -> list[str]:
    """Every input that moved after the build. Empty means fresh."""
    reasons = []
    for label, pathspec in (("backend production code", BACKEND_INPUTS),
                            ("frontend source", FRONTEND_INPUTS),
                            ("the spec", SPEC_INPUT)):
        when = _git_epoch(pathspec)
        if when > base_epoch:
            reasons.append("%s moved %s after the build" % (
                label, _ago(when - base_epoch)))
    dist = REPO / "frontend" / "dist" / "index.html"
    if dist.is_file() and dist.stat().st_mtime > base_epoch:
        reasons.append("frontend/dist was rebuilt %s after the exe was packaged"
                       % _ago(dist.stat().st_mtime - base_epoch))
    return reasons


def _ago(seconds: float) -> str:
    """The size of a gap, in words.

    Sub-hour gaps are printed in minutes. K-37 found the previous version
    describing a 390-second gap as "0 hours": a gate reporting that the build
    is stale by zero hours teaches its reader to stop believing it.
    """
    if seconds < 3600:
        return "%.0f minutes" % (seconds / 60.0)
    h = seconds / 3600.0
    return "%.0f hours" % h if h < 48 else "%.1f days" % (h / 24.0)


def _exe() -> Path | None:
    for p in EXE_CANDIDATES:
        if p.is_file():
            return p
    return None


def _toc_names(exe: Path) -> list[str]:
    from PyInstaller.archive.readers import CArchiveReader

    toc = CArchiveReader(str(exe)).toc
    return list(toc.keys()) if hasattr(toc, "keys") else [e[-1] for e in toc]


needs_exe = pytest.mark.skipif(
    _exe() is None,
    reason="no build to inspect - run `npm run build` in frontend/, then "
           "`pyinstaller elysium_onefile.spec` from backend/",
)


# ── the logic, proven without a build ────────────────────────────────────────

#: A stand-in archive listing for the pure tests. Deliberately NOT the derived
#: expectation: a control fed the real thing would agree with itself.
SYNTHETIC = {
    "frontend_dist": {"frontend_dist/index.html",
                      "frontend_dist/assets/app.js",
                      "frontend_dist/assets/app.css"},
    "tts_worker": {"tts_worker/one.py", "tts_worker/two.py"},
    "tts/requirements": {"tts/requirements/one.txt"},
}


def _flat(expected) -> list[str]:
    return sorted(n for group in expected.values() for n in group)


def test_a_complete_archive_is_reported_complete():
    assert payload_gaps(SYNTHETIC, _flat(SYNTHETIC)) == []
    assert forbidden_in(_flat(SYNTHETIC)) == []


def test_a_dropped_javascript_bundle_is_named():
    """The hole K-37 opened this file up to fix.

    The old expectation named one file under frontend_dist/, so an archive
    carrying the document without its script - a window that paints nothing -
    was reported complete. Both of these must fail now: the set comparison
    because a member is gone, and the degraded check because no script is left.
    """
    names = [n for n in _flat(SYNTHETIC) if not n.endswith(".js")]
    assert payload_gaps(SYNTHETIC, names) == ["missing: frontend_dist/assets/app.js"]
    assert blank_page_gaps(names) == ["missing: any frontend_dist script"]


def test_a_dropped_frontend_is_named():
    """The exact failure the spec's `datas` list exists to prevent."""
    names = [n for n in _flat(SYNTHETIC) if not n.startswith("frontend_dist/")]
    assert sorted(payload_gaps(SYNTHETIC, names)) == sorted(
        "missing: " + n for n in SYNTHETIC["frontend_dist"])
    assert sorted(blank_page_gaps(names)) == sorted([
        "missing: frontend_dist/index.html",
        "missing: any frontend_dist script",
        "missing: any frontend_dist stylesheet",
    ])


def test_a_build_with_no_voice_is_named():
    """The HTTP checks in run_app's selftest pass on a build that cannot
    speak; the worker scripts and requirements are the half they miss."""
    names = [n for n in _flat(SYNTHETIC)
             if not n.startswith(("tts_worker/", "tts/"))]
    gone = payload_gaps(SYNTHETIC, names)
    assert sorted(gone) == sorted(
        "missing: " + n
        for n in SYNTHETIC["tts_worker"] | SYNTHETIC["tts/requirements"])


def test_an_entry_the_disk_no_longer_has_is_named():
    """A stale dist leaves an old content-hashed bundle in the archive. That is
    the same defect as a missing one, seen from the other side, and reporting
    only absences would call it healthy."""
    names = _flat(SYNTHETIC) + ["frontend_dist/assets/app-OLD.js"]
    assert payload_gaps(SYNTHETIC, names) == [
        "unexpected: frontend_dist/assets/app-OLD.js"]


def test_windows_separators_do_not_hide_an_entry():
    """PyInstaller reports backslashes on Windows. A comparison that did not
    normalise would report every required entry as missing on the machine the
    build actually happens on."""
    assert payload_gaps(
        SYNTHETIC, [n.replace("/", "\\") for n in _flat(SYNTHETIC)]) == []


def test_the_expectation_is_derived_from_disk_and_says_what_it_cannot_decide():
    """The derivation applies the spec's rules rather than repeating its output.

    Checked against the rules, not against a copy of the answer: the worker set
    must exclude the package marker that IS on disk, and every derived name
    must correspond to a real file. `undecidable` must name any group whose
    disk side is missing, so an absent dist can never read as a satisfied
    expectation.
    """
    expected, undecidable = expected_payload()

    worker = REPO / "backend" / "tts" / "worker"
    assert (worker / "__init__.py").is_file(), (
        "the rule being checked is that __init__.py is EXCLUDED; if the file "
        "is gone this test proves nothing")
    assert "tts_worker/__init__.py" not in expected["tts_worker"]
    assert expected["tts_worker"], "the worker expectation is vacuous"
    for name in expected["tts_worker"]:
        assert (worker / name.split("/", 1)[1]).is_file(), name

    for group in ("frontend_dist", "tts_worker", "tts/requirements"):
        assert (group in expected) != (group in undecidable), (
            "%s is neither expected nor declared undecidable" % group)


def test_smuggled_data_is_caught_in_any_directory():
    for bad in ("app.db", "sub/app.db", "x/y/salt.bin", "logs/uvicorn.log", "a/.env"):
        assert forbidden_in([bad]) == [bad], bad


def test_an_innocent_name_is_not_mistaken_for_data():
    for ok in ("frontend_dist/index.html", "tts/requirements/fish_s2.txt",
               "app.dbf", "envelope.py", "catalog.py"):
        assert forbidden_in([ok]) == [], ok


def test_nothing_is_stale_against_a_build_from_the_future():
    """Positive control for the staleness reader: given a build stamped far
    ahead of every input, it must find nothing. If this ever fails, the git
    plumbing changed and the real check below is reporting noise."""
    assert stale_reasons(time.time() + 86_400 * 3650) == []


def test_everything_is_stale_against_a_build_from_1970():
    """The other direction, so a reader that always returns empty is caught."""
    assert stale_reasons(0) != []


def test_the_reader_reacts_to_the_real_backend_history_at_the_boundary():
    """Positive control with the SHIPPED pathspec, not a synthetic one.

    The two tests above answer "can this function read git at all", which is
    true whatever the pathspec says - K-34 named that shape. This one bisects
    the real boundary: one second before the newest commit touching
    BACKEND_INPUTS the reader must name it, and exactly at that stamp it must
    not, because the comparison is strict. A pathspec pointing at the wrong
    tracked set fails the first half.
    """
    newest = _git_epoch(BACKEND_INPUTS)
    named = "backend production code"
    assert any(named in r for r in stale_reasons(newest - 1))
    assert not any(named in r for r in stale_reasons(newest))


def test_a_sub_hour_gap_is_not_reported_as_zero_hours():
    """K-37: the commit that exposed the mtime bug printed "0 hours"."""
    assert _ago(390) == "6 minutes"
    assert "0 hours" not in _ago(390)
    assert _ago(7200) == "2 hours"


# ── the real artefact ────────────────────────────────────────────────────────

@needs_exe
def test_the_build_carries_the_frontend_and_the_voice_payload():
    names = _toc_names(_exe())
    assert len(names) > 100, "the archive reader returned almost nothing"

    expected, undecidable = expected_payload()
    assert expected, "nothing could be derived - the comparison would be vacuous"
    assert payload_gaps(expected, names) == [], (
        "the build and the disk it was made from disagree")

    if "frontend_dist" in undecidable:
        # No dist to compare against, so assert the degraded form rather than
        # nothing at all. Named in the message so a green run is not mistaken
        # for the strong check having passed.
        assert blank_page_gaps(names) == [], (
            "no frontend/dist on disk, so only the blank-page check ran, and "
            "it failed")


@needs_exe
def test_the_excluded_files_are_absent_from_the_build():
    """Keeps NOT_AN_INPUT from drifting into an excuse.

    Weak on purpose, and the docstring at the top says so: absence from the
    archive does not by itself prove a file cannot affect the build. It does
    prove nobody started packaging one of these while the staleness reader went
    on ignoring it.
    """
    have = {_norm(n).rsplit("/", 1)[-1] for n in _toc_names(_exe())}
    packaged = sorted(p for p in NOT_AN_INPUT if p.rsplit("/", 1)[-1] in have)
    assert packaged == [], (
        "these are excluded from the staleness check but the build carries "
        "them, so a change to one would ship unnoticed: %s" % packaged)


@needs_exe
def test_the_build_carries_nobody_s_data():
    assert forbidden_in(_toc_names(_exe())) == []


@needs_exe
def test_the_shipped_copy_is_the_copy_that_was_built():
    """The spec writes backend/dist; the file a downloader runs is the one at
    the repository root. Every other test here reads whichever it finds
    first, so if those two ever diverge the whole gate would describe the
    wrong binary - and describe it accurately, which is worse.

    This is not hypothetical. It happened once already: KADEME 21b rebuilt
    the exe, backend/dist got the new one, and the gate stayed red because
    the root copy was still the August 10 build. Rebuilding is not finished
    until the root copy is refreshed, and that is what this pins.
    """
    root, dist = EXE_CANDIDATES
    if not (root.is_file() and dist.is_file()):
        pytest.skip("only one copy present, nothing to compare")
    import hashlib

    a = hashlib.md5(root.read_bytes()).hexdigest()
    b = hashlib.md5(dist.read_bytes()).hexdigest()
    assert a == b, (
        "the shipped root copy and the freshly built backend/dist copy are "
        "different files (%s vs %s). Copy backend/dist/Elysium.exe over the "
        "root one - a build that stops at dist/ ships nothing." % (a[:12], b[:12])
    )


@needs_exe
def test_the_build_does_not_carry_the_build_machine_s_home_path():
    """The artefact half of hygiene rule H-04, which only reads source text.

    PyInstaller writes the absolute source path into every module's
    `co_filename`, and both specs keep `disable_windowed_traceback=False`, so
    a crash box can put it on screen.

    The home PATH is searched, not the bare user name. Measured in KADEME 21b:
    on this machine the account is called `USER`, and that four-letter string
    occurs inside `USER32.dll` in any Windows binary. Searching the name alone
    reports a leak on every build forever.

    Scope, measured in K-37: this looks for THIS machine's home path. Third
    party wheels carry their own build machines' paths (a `runneradmin` under
    Cargo, a `benedikt` under uv) and those are not ours to remove, so the
    check is deliberately narrow rather than being widened into a rule that can
    only ever be suppressed.
    """
    exe = _exe()
    data = exe.read_bytes()
    user = os.environ.get("USERNAME", "")
    needles = [r"C:\Users" + "\\" + user] if user else []
    for needle in needles:
        for enc in ("ascii", "utf-16-le"):
            assert data.count(needle.encode(enc)) == 0, (
                "%r appears in %s as %s - a crash box would show the build "
                "machine's home path" % (needle, exe.name, enc)
            )


@needs_exe
def test_the_frozen_build_boots_and_serves_its_own_frontend():
    """Runs the exe's own headless self-check, which until KADEME 21b nobody
    ran automatically - the mechanism existed in run_app.py and its result was
    wired to nothing.

    This is the only test in the repo that executes the shipped binary. It
    proves what no source test can: that the FROZEN interpreter resolves its
    imports, that the SQLCipher native library loads inside the bundle, that
    the server comes up, and that the packaged frontend is served.

    ELYSIUM_DATA_DIR is redirected at a throwaway folder. The real vault is
    never opened, and this test asserts that afterwards - a boot check that
    quietly touched the user's database would be a worse bug than anything it
    could find.
    """
    import hashlib
    import subprocess
    import tempfile

    vault = Path(os.environ["LOCALAPPDATA"]) / "Elysium" / "app.db"
    before = hashlib.md5(vault.read_bytes()).hexdigest() if vault.is_file() else None

    with tempfile.TemporaryDirectory(prefix="elysium-selftest-") as tmp:
        env = dict(os.environ, ELYSIUM_SELFTEST="1", ELYSIUM_DATA_DIR=tmp)
        try:
            done = subprocess.run([str(_exe())], env=env, capture_output=True,
                                  text=True, timeout=300)
        except subprocess.TimeoutExpired:
            pytest.fail("the frozen build did not finish its self-check in 300s")

    line = next((l for l in done.stdout.splitlines() if "SELFTEST" in l), "")
    assert done.returncode == 0, "self-check exited %d: %s" % (done.returncode, line)
    for claim in ("healthz=True", "root_serves_spa=True", "voice_payload=True"):
        assert claim in line, "self-check did not report %s: %r" % (claim, line)

    if before is None:
        # No vault on this machine, so there was nothing to corrupt - but the
        # boot check must not have CREATED one either. Written this way on
        # purpose: the first version simply skipped the assertion when the
        # file was absent, which meant the one line guarding user data never
        # ran on exactly the machines least likely to have a vault.
        assert not vault.is_file(), (
            "the self-check created a vault at the real path despite "
            "ELYSIUM_DATA_DIR pointing elsewhere"
        )
    else:
        assert hashlib.md5(vault.read_bytes()).hexdigest() == before, (
            "the self-check wrote to the real vault despite ELYSIUM_DATA_DIR"
        )


@needs_exe
def test_the_build_is_not_older_than_what_went_into_it():
    """The staleness trigger.

    Without it the gate above is worse than nothing: it would keep reporting
    a healthy build while describing an exe from weeks ago. A stale artefact
    is the failure mode a gate that only checks CONTENT cannot see.
    """
    base, clock = exe_epoch(_exe())
    reasons = stale_reasons(base)
    assert reasons == [], (
        "the committed exe predates its own inputs (measured by its %s time), "
        "so every check above describes an artefact nobody would ship:\n  "
        % clock + "\n  ".join(reasons)
        + "\nRebuild: `npm run build` in frontend/, then "
          "`pyinstaller elysium_onefile.spec` from backend/."
    )
