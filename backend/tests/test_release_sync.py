"""Audit KÖK 12: the documents that describe the app drifted from the app.

Each of these was a claim somebody would act on - a parameter list a frontend
author reads before adding a control, a version a build stamps, a spec a
release is cut from. None of them had anything checking they were still true.

Deliberately NOT here: the untracked source tree, which is the largest finding
of KÖK 12 and cannot be closed by a test. See the note at the bottom.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
CONTRACT = REPO / "docs" / "frontend_contract.md"


def _read(path: Path) -> str:
    if not path.is_file():
        pytest.skip(f"{path.name} is not in this checkout")
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# the generation-parameter allowlist
# ---------------------------------------------------------------------------

def test_the_contract_lists_every_parameter_the_backend_accepts():
    """The table said seven; openrouter.py accepted and forwarded ten. A
    frontend author reading the contract would not know min_p, top_a or either
    penalty could be sent at all."""
    from openrouter import _PARAM_SPEC

    body = _read(CONTRACT)
    table = body.split("## Generation Parameters (backend allowlist)", 1)[1]
    table = table.split("\n---", 1)[0]

    missing = sorted(
        name for name in _PARAM_SPEC
        if not re.search(r"^\|\s*" + re.escape(name) + r"\s*\|", table, re.M)
    )
    assert not missing, f"accepted but undocumented: {missing}"


def test_the_contract_does_not_promise_parameters_that_are_rejected():
    """The other direction: a documented knob the backend drops is a control
    somebody builds and then cannot make work."""
    from openrouter import _PARAM_SPEC

    body = _read(CONTRACT)
    table = body.split("## Generation Parameters (backend allowlist)", 1)[1]
    table = table.split("\n---", 1)[0]

    #: Documented on purpose as NOT forwarded - they are app-level.
    app_level = {"context_budget_tokens", "stop", "Param"}
    listed = set(re.findall(r"^\|\s*([a-z_]+)\s*\|", table, re.M)) - app_level
    unknown = sorted(listed - set(_PARAM_SPEC))
    assert not unknown, f"documented but not accepted: {unknown}"


# ---------------------------------------------------------------------------
# the endpoint list
# ---------------------------------------------------------------------------

#: Not part of the frontend's contract: infrastructure the SPA never calls.
_NOT_IN_CONTRACT = {
    "/healthz",
    "/api/v1/openapi.json",
}


def _live_routes() -> set[tuple[str, str]]:
    from main import app

    out: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/v1/"):
            continue
        short = path[len("/api/v1"):]
        for method in getattr(route, "methods", set()) or set():
            if method in ("HEAD", "OPTIONS"):
                continue
            out.add((method, short))
    return {r for r in out if r[1] not in _NOT_IN_CONTRACT}


def test_every_route_the_app_serves_appears_in_the_contract():
    """The list had drifted by six endpoints - speak_stream, speak_live, both
    halves of tag-prefs, pronunciations, stop-sequences and proxy/alias - so a
    frontend author reading the contract could not learn that the Speak
    button's actual endpoint existed at all. Checked against the ROUTER, which
    is the only thing that decides what is served.

    PATH shapes only, not (method, path) pairs. The document describes some
    endpoints as table rows and others in prose, with combined forms like
    `GET|POST /tts/active`, and turning that into one house style is a
    formatting opinion rather than a correctness one. This catches the class
    that actually happened - a whole endpoint nobody wrote down - and it does
    not catch "documented for GET, added for POST". Said plainly rather than
    implied, so nobody reads more into a green run than is there.
    """
    def shape(path: str) -> str:
        # Param NAMES differ by design: the router says `{engine_id}`, the
        # prose says `{engine}`.
        return re.sub(r"\{[^}]*\}", "{}", path).rstrip("/")

    body = shape(_read(CONTRACT))
    missing = sorted(
        {shape(path) for _method, path in _live_routes() if shape(path) not in body}
    )
    assert not missing, f"served but undocumented: {missing}"


# ---------------------------------------------------------------------------
# the version, in two files that must agree
# ---------------------------------------------------------------------------

def test_the_windows_version_resource_matches_package_json():
    """version_info.txt carries 1.1.0.0 by hand in four places while
    vite.config.ts derives the version from package.json. Nothing checked they
    agreed, so the next bump would ship an exe claiming the previous release.
    """
    pkg = json.loads(_read(REPO / "frontend" / "package.json"))
    version = str(pkg["version"]).strip()
    parts = version.split(".")
    assert len(parts) == 3, f"expected x.y.z, got {version!r}"
    expected_tuple = tuple(int(p) for p in parts) + (0,)

    body = _read(BACKEND / "version_info.txt")
    quads = set(re.findall(r'"(\d+\.\d+\.\d+\.\d+)"', body))
    struct = set(re.findall(r"filevers=\((\d+, ?\d+, ?\d+, ?\d+)\)", body))
    struct |= set(re.findall(r"prodvers=\((\d+, ?\d+, ?\d+, ?\d+)\)", body))

    expected_str = ".".join(str(n) for n in expected_tuple)
    assert quads, "no version strings found - has the file changed shape?"
    assert quads == {expected_str}, (
        f"version_info.txt says {sorted(quads)}, package.json says {version}"
    )
    for raw in struct:
        got = tuple(int(n) for n in raw.replace(" ", "").split(","))
        assert got == expected_tuple, f"filevers/prodvers {got} != {expected_tuple}"


# ---------------------------------------------------------------------------
# the two specs a release can be cut from
# ---------------------------------------------------------------------------

_SPEC_A = BACKEND / "elysium.spec"
_SPEC_B = BACKEND / "elysium_onefile.spec"


def _spec_payload(body: str) -> dict[str, set[str]]:
    """The parts that decide whether the exe can actually run.

    Compared as SETS of names rather than as text: the two files legitimately
    differ in how they assemble (one folder vs one file), and pinning the whole
    body would make every layout change a test failure. What they may never
    differ in is what goes IN.
    """
    # Both quote styles, and this is not a nicety. These two patterns matched
    # ONLY single quotes while both spec files are written with double ones,
    # so every comparison below was set() == set() - a drift guard that could
    # not fail, sitting under a docstring about catching drift. It stayed
    # green through a real divergence. `collect_all` had it right all along,
    # which is what makes the omission visible.
    quoted = r"['\"]([\w\.]+)['\"]"
    quoted_dashed = r"['\"]([\w\.\-]+)['\"]"

    def _names(key: str, pattern: str) -> set[str]:
        blocks = re.findall(rf"{key}\s*=\s*\[(.*?)\]", body, re.S)
        if not blocks:
            return set()
        return set(re.findall(pattern, blocks[0]))

    # Neither spec calls collect_all on a literal - both loop a tuple of
    # package names and call collect_all(pkg). Matching a literal argument
    # therefore found nothing, in both files, permanently. These packages
    # carry the native SQLCipher library and pywebview; adding one to a single
    # spec ships an exe that cannot open the vault, which is precisely what
    # this comparison exists to prevent.
    bundled = re.findall(r"for\s+pkg\s+in\s*\((.*?)\)\s*:", body, re.S)

    return {
        "hiddenimports": _names("hiddenimports", quoted),
        "collect_all": set(re.findall(quoted, bundled[0])) if bundled else set(),
        "excludes": _names("excludes", quoted_dashed),
    }


def test_the_spec_comparison_can_actually_fail():
    """Guard the guard.

    The comparison below is only worth having if it SEES anything. All three
    of its keys parsed to nothing and it passed on empty sets - so it stayed
    green through a real divergence. This asserts the parser finds real names
    in each key, so the same silence cannot come back unnoticed.
    """
    payload = _spec_payload(_read(_SPEC_B))
    assert "attachments_service" in payload["hiddenimports"]
    assert "sqlcipher3" in payload["collect_all"], (
        "the bundled-package list parsed as empty - the exe could ship without "
        "the native SQLCipher library and nothing here would notice")
    assert payload["excludes"], "excludes parsed as empty - the regex is blind again"


def test_the_two_specs_bundle_the_same_things():
    """They are ~90% identical and the only guard was that two strings appeared
    SOMEWHERE in the file. Change one and not the other and the suite stays
    green - while the exe that actually ships is the one nobody tested.
    """
    a, b = _spec_payload(_read(_SPEC_A)), _spec_payload(_read(_SPEC_B))
    for key in ("hiddenimports", "collect_all", "excludes"):
        assert a[key] == b[key], (
            f"{key} differs between the two specs: "
            f"only in elysium.spec {sorted(a[key] - b[key])}, "
            f"only in elysium_onefile.spec {sorted(b[key] - a[key])}"
        )


def test_both_specs_carry_the_voice_worker_and_its_requirements():
    """Without these the packaged app installs no engine and speaks nothing -
    which is the entire reason the spec files are not the PyInstaller default.
    """
    for spec in (_SPEC_A, _SPEC_B):
        body = _read(spec)
        assert "tts_worker" in body, spec.name
        assert "tts/requirements" in body or "tts\\\\requirements" in body, spec.name


def test_the_readme_documents_the_spec_the_release_is_built_from():
    """The published Elysium.exe is the one-file build, and the README only
    ever named the one-folder spec - so the instructions could not reproduce
    the artefact sitting next to them."""
    body = _read(REPO / "README.md")
    assert "elysium_onefile.spec" in body, (
        "the shipped exe is built from elysium_onefile.spec and the README "
        "does not mention it"
    )


# ---------------------------------------------------------------------------
# NOT covered here, and it is the biggest one
# ---------------------------------------------------------------------------
#
# KÖK 12's first finding is that ~104 source files - the whole tts/ package,
# speech_prep.py, voice_tags.py, keyring_service.py and 40+ tests - are
# untracked, so a fresh clone dies on `import main`. No test run from inside
# the working tree can see that: the suite reads the working tree, which is
# exactly the copy that HAS the files. The gate for it is `git archive HEAD`
# into a temp dir followed by an import, and it can only be written once the
# files are committed - which is the user's call, not this suite's.
