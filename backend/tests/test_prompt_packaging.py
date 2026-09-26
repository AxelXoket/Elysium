"""The packaged exe carries every shared/ file the backend opens at run time.

prompt_sanitizer.py and prompt_layer.py read shared/prompt_sanitizer.json and
shared/prompt_layer_text.txt at import, and completions.py and vault.py
import them, so a frozen build without those files dies at startup. Three
behaviours, measured rather than read off the spec text:

1. which shared/ files a fresh backend process actually opens (recorded);
2. that both PyInstaller specs, EXECUTED with PyInstaller's hooks stubbed,
   put each of them under `shared` in `datas`;
3. that a process pretending to be frozen, with _MEIPASS built from exactly
   those datas, imports the prompt modules (and fails without them: the
   positive control that shows step 3 can go red).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

BACKEND = Path(__file__).resolve().parent.parent
SPECS = ("elysium.spec", "elysium_onefile.spec")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(code: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run([sys.executable, "-c", code], cwd=str(BACKEND), env=env,
                          capture_output=True, text=True, timeout=120,
                          creationflags=_NO_WINDOW)


_RECORD = r'''
import builtins, io, json, os, sys
seen = set()
real_open = io.open
def spy(file, *a, **k):
    try:
        p = os.path.abspath(os.fspath(file))
        if os.path.basename(os.path.dirname(p)) == "shared":
            seen.add(os.path.basename(p))
    except TypeError:
        pass
    return real_open(file, *a, **k)
builtins.open = spy
io.open = spy          # pathlib.Path.read_text goes through io.open
import main  # noqa: F401  the whole app, as the exe starts it
print(json.dumps(sorted(seen)))
'''


@pytest.fixture(scope="module")
def opened_shared_files() -> list[str]:
    out = _run(_RECORD)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def _spec_datas(spec: str) -> list[tuple[str, str]]:
    hooks = types.ModuleType("PyInstaller.utils.hooks")
    hooks.collect_all = lambda pkg: ([], [], [])
    hooks.collect_submodules = lambda pkg: []
    stubs = {"PyInstaller": types.ModuleType("PyInstaller"),
             "PyInstaller.utils": types.ModuleType("PyInstaller.utils"),
             "PyInstaller.utils.hooks": hooks}
    g = {"SPECPATH": str(BACKEND), "__name__": "__spec__",
         "Analysis": mock.MagicMock(), "PYZ": mock.MagicMock(),
         "EXE": mock.MagicMock(), "COLLECT": mock.MagicMock()}
    src = (BACKEND / spec).read_text(encoding="utf-8")
    with mock.patch.dict(sys.modules, stubs):
        exec(compile(src, spec, "exec"), g)  # noqa: S102  the spec is ours
    return [(str(s), str(d)) for s, d in g["datas"]]


def test_ground_the_backend_opens_shared_files_at_startup(opened_shared_files):
    assert "prompt_sanitizer.json" in opened_shared_files
    assert "prompt_layer_text.txt" in opened_shared_files


@pytest.mark.parametrize("spec", SPECS)
def test_every_opened_shared_file_is_in_the_spec(spec, opened_shared_files):
    shipped = {Path(src).name for src, dest in _spec_datas(spec) if dest == "shared"}
    missing = set(opened_shared_files) - shipped
    assert not missing, f"{spec} does not ship {sorted(missing)}"
    for src, dest in _spec_datas(spec):
        if dest == "shared":
            assert Path(src).is_file(), src


_FROZEN = r'''
import sys
sys.frozen = True
sys._MEIPASS = sys.argv[1] if len(sys.argv) > 1 else r"{meipass}"
import prompt_layer, prompt_sanitizer
assert prompt_sanitizer.shared_path("x").parent.parent == __import__("pathlib").Path(sys._MEIPASS)
print("ok", len(prompt_sanitizer.FOLD), len(prompt_layer.LAYER))
'''


@pytest.mark.parametrize("spec", SPECS)
def test_a_frozen_process_imports_with_exactly_the_spec_datas(spec, tmp_path):
    meipass = tmp_path / "meipass"
    for src, dest in _spec_datas(spec):
        if dest == "shared":
            (meipass / dest).mkdir(parents=True, exist_ok=True)
            shutil.copy(src, meipass / dest / Path(src).name)
    out = _run(_FROZEN.replace("{meipass}", str(meipass)))
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.startswith("ok")


def test_positive_control_a_frozen_process_without_them_fails(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    out = _run(_FROZEN.replace("{meipass}", str(empty)))
    assert out.returncode != 0
    assert "prompt_sanitizer.json" in out.stderr or "FileNotFoundError" in out.stderr
