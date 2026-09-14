"""verify/verify_tts_resolve.py - do the shipped voice freezes still install?

Each engine's requirements file is a freeze of an environment that was
measured working, and tts/provision.py hands it to the resolver with an
overrides file where a package's own metadata contradicts it. That leaves one
way for a freeze to go stale without any test noticing: an artefact it names
disappears from the index, the index stops offering a wheel for this platform,
or a package's metadata grows a new objection the overrides do not cover. A
user finds that out ten minutes into a multi-gigabyte download. This finds it
out first. (Fish's source tree is fetched by the installer itself and is not
part of a dry run; its content pin is checked at install time.)

It builds an empty interpreter in a temporary folder (uv's managed build of
the pinned Python is fetched into that same folder, so nothing is left behind
in the checkout) and runs the SAME command
the installer runs, provision.install_argv, with `--dry-run` added: uv
resolves and reports what it would install, and downloads nothing but
metadata. It needs the network and a uv at least as new as the pinned one
(provision.find_uv refuses an older one, the same way the installer does); it
does NOT open the app and does NOT need the vault. The child is handed the
same stripped environment the installer hands its own child, so a developer's
UV_INDEX_URL cannot change what this resolves.

    .venv/Scripts/python.exe verify/verify_tts_resolve.py            # all three
    .venv/Scripts/python.exe verify/verify_tts_resolve.py fish_s2    # one

Exit 0 when every freeze resolves, 1 when any does not, 2 when there is no uv
to ask. Runs by hand and before a release, never from the pre-commit hook: it
is a network check and the gate lane is offline by rule.

Measured 8 September 2026 with uv 0.11.7, which is the reason this exists:
with dependency resolution ON, fish_s2 and chatterbox were unsatisfiable and
only xtts_v2 resolved. Nothing in the suite could see it, because the suite
replaces the subprocess and asserts on the command line.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from tts import provision  # noqa: E402

_WOULD_INSTALL = re.compile(r"Would install (\d+) packages?")


def _flags() -> int:
    return provision.CREATE_NO_WINDOW if provision.IS_WINDOWS else 0


def _tail(text: str, lines: int = 12) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def resolve_one(uv: str, engine_id: str, workdir: Path, env: dict) -> tuple[bool, str]:
    """(ok, one-line summary). The full uv output is printed by the caller
    only on failure, so a green run stays readable."""
    plan = provision.plan(engine_id)
    venv = workdir / engine_id
    python = venv / ("Scripts/python.exe" if provision.IS_WINDOWS else "bin/python")
    made = subprocess.run(
        provision.venv_argv(uv, venv, plan.python_version),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, creationflags=_flags(), timeout=600,
    )
    if made.returncode != 0:
        return False, "could not build the scratch interpreter:\n" + _tail(
            made.stdout + made.stderr)
    argv = provision.install_argv(uv, python, plan) + ["--dry-run"]
    done = subprocess.run(
        argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, creationflags=_flags(), timeout=900,
    )
    out = (done.stdout or "") + (done.stderr or "")
    if done.returncode != 0:
        return False, _tail(out)
    match = _WOULD_INSTALL.search(out)
    listed = len(provision.requirement_lines(engine_id))
    count = int(match.group(1)) if match else -1
    if count != listed:
        # A transitive dependency missing from the freeze resolves to the
        # newest of the day and the install still "works"; the freeze is a
        # freeze only while the resolver installs exactly what it lists.
        return False, (f"the resolver would install {count} packages but the "
                       f"freeze lists {listed}; something floats:\n" + _tail(out))
    return True, f"{count} packages would install, one per pinned line"


def main(argv: list[str]) -> int:
    engines = argv or sorted(provision.ENGINES)
    unknown = [e for e in engines if e not in provision.ENGINES]
    if unknown:
        print(f"unknown engine(s): {', '.join(unknown)}; "
              f"known: {', '.join(sorted(provision.ENGINES))}")
        return 2
    uv = provision.find_uv()
    if not uv:
        print(f"no uv {provision.UV_VERSION} or newer on this machine to ask; the app "
              "would download its pinned copy at install time, this check does not")
        return 2
    print(f"uv: {uv}")
    env = {k: v for k, v in os.environ.items() if k not in provision._ENV_NETWORK_STRIP}
    env.update(provision.child_env_base())
    failures = 0
    with tempfile.TemporaryDirectory(prefix="elysium-resolve-",
                                     ignore_cleanup_errors=True) as tmp:
        # Not the app's folders: in a development checkout child_env_base()
        # points at backend/voice, and a managed interpreter deposited there
        # (66 MB, with tcl demos in Python 2 syntax) broke the suite's source
        # sweep. Measured 8 September 2026.
        env["UV_PYTHON_INSTALL_DIR"] = str(Path(tmp) / "python")
        env["UV_CACHE_DIR"] = str(Path(tmp) / "uv-cache")
        for engine_id in engines:
            ok, summary = resolve_one(uv, engine_id, Path(tmp), env)
            print(f"{engine_id}: {'ok' if ok else 'FAIL'}")
            for line in summary.splitlines():
                print(f"    {line}")
            failures += 0 if ok else 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
