"""test_artifact_gate.py - the one test that looks at a real build output.

KADEME 21b. Every other test in this repo reads source. Nothing ever opened
the thing the user actually double-clicks, so the spec's `datas` list could
drop `frontend_dist` and the whole suite would stay green on an exe that
serves a blank page - or one that cannot speak, because the worker scripts
and the pinned requirements are plain data files PyInstaller omits unless the
spec says otherwise.

This lives in pytest on purpose. KADEME 07 measured the cost of the other
arrangement: sixteen scripts under backend/verify/, nobody running them,
three already dead. A gate that needs somebody to remember it is not a gate.

Two halves:

  * The classifying and staleness logic is pure and is exercised below with
    synthetic input. It is proven whether or not an exe exists.
  * The assertions about the real artefact skip, loudly, when there is no
    exe to look at. Skipping is right here: a missing build is not a broken
    build, and a suite that is red on every clean checkout gets ignored.

STALENESS uses git commit times for tracked source, not filesystem mtimes.
Measured the hard way in KADEME 21a: a `git checkout` that restores a file to
its committed bytes still moves its mtime, so an mtime comparison reported
production code as "changed" when nothing about it had changed. Commit time
answers the question actually being asked - did the CONTENT move.
`frontend/dist` is the exception: it is a build output, untracked, so its
mtime is all there is.
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

#: Present or the exe is broken in a way no source test can see.
REQUIRED = (
    "frontend_dist/index.html",
    "tts_worker/fish_s2.py",
    "tts_worker/chatterbox.py",
    "tts_worker/xtts_v2.py",
    "tts_worker/_dsp.py",
    "tts_worker/_wire.py",
    "tts_worker/_fit.py",
    "tts/requirements/fish_s2.txt",
    "tts/requirements/chatterbox.txt",
    "tts/requirements/xtts_v2.txt",
)

#: Absent, or the build shipped somebody's data.
FORBIDDEN_NAMES = ("app.db", ".env", "salt.bin", "verifier.bin")
FORBIDDEN_SUFFIX = (".log",)


def _norm(name: str) -> str:
    return name.replace("\\", "/")


def missing_from(names) -> list[str]:
    """Required entries the archive does not carry."""
    have = {_norm(n) for n in names}
    return [r for r in REQUIRED if r not in have]


def forbidden_in(names) -> list[str]:
    """Entries the archive must never carry."""
    out = []
    for n in names:
        tail = _norm(n).rsplit("/", 1)[-1].lower()
        if tail in FORBIDDEN_NAMES or tail.endswith(FORBIDDEN_SUFFIX):
            out.append(n)
    return out


def _git_epoch(pathspec: list[str]) -> int:
    """Commit time of the newest commit touching `pathspec`, or 0."""
    out = subprocess.run(
        ["git", "log", "-1", "--format=%ct", "--", *pathspec],
        cwd=REPO, capture_output=True, text=True,
    ).stdout.strip()
    return int(out) if out.isdigit() else 0


def stale_reasons(exe_epoch: float) -> list[str]:
    """Every input that moved after the exe was built. Empty means fresh."""
    reasons = []
    backend = _git_epoch(["backend", ":(exclude)backend/tests", ":(exclude)backend/verify"])
    frontend = _git_epoch(["frontend/src", ":(exclude)frontend/src/test",
                           "frontend/package.json", "frontend/vite.config.ts",
                           "frontend/index.html"])
    spec = _git_epoch(["backend/elysium_onefile.spec"])
    for label, when in (("backend production code", backend),
                        ("frontend source", frontend),
                        ("the spec", spec)):
        if when and when > exe_epoch:
            reasons.append("%s moved %s after the build" % (
                label, _ago(when - exe_epoch)))
    dist = REPO / "frontend" / "dist" / "index.html"
    if dist.is_file() and dist.stat().st_mtime > exe_epoch:
        reasons.append("frontend/dist was rebuilt %s after the exe was packaged"
                       % _ago(dist.stat().st_mtime - exe_epoch))
    return reasons


def _ago(seconds: float) -> str:
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

def test_a_complete_archive_is_reported_complete():
    assert missing_from(REQUIRED) == []
    assert forbidden_in(REQUIRED) == []


def test_a_dropped_frontend_is_named():
    """The exact failure the spec's `datas` list exists to prevent."""
    without = [n for n in REQUIRED if not n.startswith("frontend_dist/")]
    assert missing_from(without) == ["frontend_dist/index.html"]


def test_a_build_with_no_voice_is_named():
    """The HTTP checks in run_app's selftest pass on a build that cannot
    speak; the worker scripts and requirements are the half they miss."""
    without = [n for n in REQUIRED if not n.startswith(("tts_worker/", "tts/"))]
    gone = missing_from(without)
    # Six worker scripts and three pinned requirement files. Spelled out
    # rather than counted, so adding an engine without its requirements file
    # fails here instead of quietly changing a number.
    assert sorted(gone) == sorted([n for n in REQUIRED
                                   if n.startswith(("tts_worker/", "tts/"))])
    assert len(gone) == 9


def test_windows_separators_do_not_hide_an_entry():
    """PyInstaller reports backslashes on Windows. A comparison that did not
    normalise would report every required entry as missing on the machine the
    build actually happens on."""
    assert missing_from([r.replace("/", "\\") for r in REQUIRED]) == []


def test_smuggled_data_is_caught_in_any_directory():
    for bad in ("app.db", "sub/app.db", "x/y/salt.bin", "logs/uvicorn.log", "a/.env"):
        assert forbidden_in([bad]) == [bad], bad


def test_an_innocent_name_is_not_mistaken_for_data():
    for ok in ("frontend_dist/index.html", "tts/requirements/fish_s2.txt",
               "app.dbf", "envelope.py", "catalog.py"):
        assert forbidden_in([ok]) == [], ok


def test_nothing_is_stale_against_a_build_from_the_future():
    """Positive control for the staleness reader: given an exe stamped far
    ahead of every input, it must find nothing. If this ever fails, the git
    plumbing changed and the real check below is reporting noise."""
    assert stale_reasons(time.time() + 86_400 * 3650) == []


def test_everything_is_stale_against_a_build_from_1970():
    """The other direction, so a reader that always returns empty is caught."""
    assert stale_reasons(0) != []


# ── the real artefact ────────────────────────────────────────────────────────

@needs_exe
def test_the_build_carries_the_frontend_and_the_voice_payload():
    exe = _exe()
    names = _toc_names(exe)
    assert len(names) > 100, "the archive reader returned almost nothing"
    assert missing_from(names) == [], "the build is missing files it needs"


@needs_exe
def test_the_build_carries_nobody_s_data():
    assert forbidden_in(_toc_names(_exe())) == []


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
    """
    exe = _exe()
    data = exe.read_bytes()
    user = os.environ.get("USERNAME", "")
    needles = [r"C:\Users"]
    if user:
        needles.append(r"C:\Users" + "\\" + user)
    for needle in needles:
        for enc in ("ascii", "utf-16-le"):
            assert data.count(needle.encode(enc)) == 0, (
                "%r appears in %s as %s - a crash box would show the build "
                "machine's home path" % (needle, exe.name, enc)
            )


@needs_exe
def test_the_build_is_not_older_than_what_went_into_it():
    """The staleness trigger.

    Without it the gate above is worse than nothing: it would keep reporting
    a healthy build while describing an exe from weeks ago. A stale artefact
    is the failure mode a gate that only checks CONTENT cannot see.
    """
    reasons = stale_reasons(_exe().stat().st_mtime)
    assert reasons == [], (
        "the committed exe predates its own inputs, so every check above "
        "describes an artefact nobody would ship:\n  " + "\n  ".join(reasons)
        + "\nRebuild: `npm run build` in frontend/, then "
          "`pyinstaller elysium_onefile.spec` from backend/."
    )
