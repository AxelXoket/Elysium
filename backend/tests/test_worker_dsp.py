"""V8-4 - the reading-speed dial.

Two halves, tested where each can actually run.

`tts/speed.py` is host-side and pure stdlib, so its routing (who applies the
rate: the engine or the DSP) is asserted here directly.

`tts/worker/_dsp.py` needs numpy, which the app venv deliberately does not have.
Its maths lives in `dsp_numeric_check.py` and is executed here through a
registered engine interpreter - real coverage on a machine with a voice engine
set up, an honest skip on one without. The shape checks below run everywhere.
"""
import ast
import functools
import importlib.util
import re
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import config
from tts import speed
from tts.base import ParamSpec

WORKER_DSP = Path(__file__).resolve().parents[1] / "tts" / "worker" / "_dsp.py"
NUMERIC = Path(__file__).resolve().parent / "dsp_numeric_check.py"


# ── the routing decision (host side, always runs) ────────────────────────────

def fish_specs():
    """Fish has no rate control of its own."""
    return [ParamSpec("temperature", "float", 0.7, "Expressiveness"),
            ParamSpec("top_p", "float", 0.8, "Top-p")]


def xtts_specs():
    return [ParamSpec("temperature", "float", 0.65, "Expressiveness"),
            ParamSpec("speed", "float", 1.0, "Speed", minimum=0.5, maximum=1.5)]


def test_an_engine_without_a_rate_knob_gets_the_dsp_path():
    plan = speed.plan(fish_specs(), 1.2)
    assert plan.native_param is None
    assert plan.uses_dsp
    assert plan.dsp_rate == pytest.approx(1.2)


def test_an_engine_with_its_own_rate_knob_never_runs_the_dsp():
    # Generating at the target pace beats reshaping the output afterwards -
    # there is simply nothing to smear.
    plan = speed.plan(xtts_specs(), 1.2)
    assert plan.native_param == "speed"
    assert not plan.uses_dsp
    assert plan.dsp_rate == pytest.approx(1.0)


def test_the_native_knob_is_hidden_from_the_settings_list():
    shown = [s.name for s in speed.hide_native(xtts_specs())]
    assert "speed" not in shown
    assert "temperature" in shown


def test_a_non_speed_engine_keeps_its_whole_settings_list():
    assert len(speed.hide_native(fish_specs())) == 2


def test_engine_values_drives_the_native_knob_and_nothing_else():
    assert speed.engine_values(xtts_specs(), 1.2) == {"speed": pytest.approx(1.2)}
    assert speed.engine_values(fish_specs(), 1.2) == {}


def test_the_engines_own_range_wins_over_ours():
    # Ours is 0.80-1.25; an engine whose knob stops at 1.1 must not be handed
    # 1.25 and left to floor it silently.
    specs = [ParamSpec("speed", "float", 1.0, "Speed", minimum=0.9, maximum=1.1)]
    assert speed.engine_values(specs, 1.25) == {"speed": pytest.approx(1.1)}


@pytest.mark.parametrize("given,want", [
    (None, 1.0), ("nonsense", 1.0), (0.1, speed.MIN_RATE),
    (9.0, speed.MAX_RATE), (float("nan"), 1.0), (1.0, 1.0),
])
def test_rate_clamping(given, want):
    assert speed.clamp(given) == pytest.approx(want)


def test_a_rate_of_one_asks_nobody_to_do_anything():
    assert not speed.plan(fish_specs(), 1.0).uses_dsp
    assert not speed.plan(fish_specs(), 1.01).uses_dsp


# ── the worker module's shape (always runs, no numpy needed) ─────────────────

# test_dsp_module_parses was deleted in KADEME 20b: a strict subset of
# test_tts_packaging.py::test_every_worker_script_parses, which parses
# every worker script including this one and carries its own floor.


# test_numpy_is_not_imported_at_module_scope was deleted in KADEME 20b.
# test_tts_packaging.py::test_no_engine_import_sits_at_module_scope walks
# every worker script (this one included) against an ENGINE_MODULES list
# that already names numpy, and carries its own floor. Strictly broader.
#
# Worth saying plainly, because the name suggested otherwise: NEITHER test
# ever measured anything. Both are ast scans for "no module-scope import".
# The startup cost they stand in for - importing numpy on a cold exe - has
# no timing test anywhere in this suite, and did not before this deletion.


@functools.lru_cache(maxsize=1)
def _dsp_module():
    """`_dsp.py` IMPORTED, not read.

    The two files cannot share a constant - `_dsp` lives in the worker tree,
    which the app venv does not have on its path - so the check has to reach
    across. It reaches by importing the module and reading the attribute,
    which is the value the worker will actually use.

    Importing it works because `_dsp` imports numpy INSIDE its functions
    rather than at module scope; nothing at import time needs a package the
    app venv lacks. An earlier version of this helper parsed the number out
    of the source text instead and said the module could not be imported at
    all - that reason was wrong, and it kept the file's last source-text read
    alive for no gain.

    The check this replaced was worse still: a SUBSTRING search.
    `MIN_RATE = 0.80` renders in an f-string as `MIN_RATE = 0.8`, so the
    needle was `"MIN_RATE = 0.8"` - and `MIN_RATE = 0.85` in `_dsp.py`
    contains it. The one divergence the test exists to catch was exactly the
    divergence it could not see.
    """
    spec = importlib.util.spec_from_file_location("_dsp_probe", WORKER_DSP)
    assert spec and spec.loader, f"cannot load {WORKER_DSP}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _worker_constant(name: str) -> float:
    """One number out of the imported `_dsp`, as a NUMBER."""
    value = getattr(_dsp_module(), name)
    assert isinstance(value, float), f"_dsp.{name} is {type(value).__name__}"
    return value


def test_the_advertised_range_matches_the_host_side_dial():
    """Two files hold the same numbers; a divergence would let the UI offer a
    rate the DSP then silently clamps away."""
    assert _worker_constant("MIN_RATE") == speed.MIN_RATE
    assert _worker_constant("MAX_RATE") == speed.MAX_RATE


def test_the_numbers_are_the_ones_the_host_actually_clamps_to():
    """POSITIVE CONTROL, and the half a value comparison still cannot give.

    Two files can agree on a number that neither of them uses. This drives
    the host's own clamp at both ends and asserts it lands on those values -
    so the constants are not just equal to each other, they are the limits
    the dial really has.
    """
    assert speed.clamp(speed.MIN_RATE - 1.0) == speed.MIN_RATE
    assert speed.clamp(speed.MAX_RATE + 1.0) == speed.MAX_RATE
    # And a rate inside the range is left alone, or "clamps to the limits"
    # would be satisfied by a function that returns a limit for everything.
    middle = (speed.MIN_RATE + speed.MAX_RATE) / 2
    assert speed.clamp(middle) == middle


def test_the_no_op_threshold_is_the_same_on_both_sides():
    """The host decides whether to SEND a rate; the worker decides whether to
    ACT on one. If those thresholds drift, a rate lands in the payload that the
    worker then ignores - a dial that moves and does nothing, which is the one
    failure mode a settings page cannot explain.

    They cannot share a constant: `_dsp` lives in the worker tree and the app
    venv has no numpy to import it with. So the agreement is asserted instead.
    """
    import routers.tts_runtime as rt

    src = WORKER_DSP.read_text(encoding="utf-8")
    m = re.search(r"RATE_EPSILON\s*=\s*([0-9.]+)", src)
    assert m, "_dsp lost its RATE_EPSILON"
    worker_eps = float(m.group(1))
    # Probe the host-side helper right at the worker's threshold.
    assert rt._dsp_noop(1.0 + worker_eps / 2) is True
    assert rt._dsp_noop(1.0 + worker_eps * 2) is False


# ── the maths (runs wherever numpy can be reached) ───────────────────────────

def _numpy_interpreter() -> str | None:
    """An interpreter that can import numpy: this one, or a registered engine.

    This deliberately reads the registry at its REAL location rather than the
    temp one `_isolated_voice_registry` provides. That shield exists so that
    discovery tests describe the code instead of the machine they run on - and
    this test is the opposite kind on purpose. It asks a question about the
    machine ("is there a python here that can import numpy?") and skips when
    the answer is no, so it can neither leak a real model into a discovery
    assertion nor pass because of one.
    """
    override = os.environ.get("ELYSIUM_DSP_TEST_PYTHON")
    if override and _has_numpy(override):
        return override
    if _has_numpy(sys.executable):
        return sys.executable
    registry = Path(config.TTS_DIR) / "runtimes.json"
    try:
        data = json.loads(registry.read_text("utf-8"))
    except Exception:                                   # noqa: BLE001
        return None
    for engine in (data.get("engines") or {}).values():
        exe = engine.get("python")
        if exe and Path(exe).is_file() and _has_numpy(exe):
            return exe
    return None


def _has_numpy(exe: str) -> bool:
    try:
        return subprocess.run([exe, "-c", "import numpy"], timeout=60,
                              capture_output=True).returncode == 0
    except Exception:                                   # noqa: BLE001
        return False


def test_dsp_numeric_checks_pass():
    exe = _numpy_interpreter()
    if exe is None:
        pytest.skip("no numpy-capable interpreter: set up a voice engine to "
                    "cover the DSP maths (tests/dsp_numeric_check.py)")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run([exe, str(NUMERIC)], capture_output=True,
                          text=True, timeout=600, env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "all dsp numeric checks passed" in proc.stdout


#: The shape of the bug this module actually shipped once: `time_stretch`
#: returning fewer samples than the rate implies, heard as a tail of digital
#: silence. Applied ONLY to a throwaway copy.
_TAIL_BUG = '''

_orig_time_stretch = time_stretch


def time_stretch(x, rate, *a, **k):
    out = _orig_time_stretch(x, rate, *a, **k)
    return out[:-700] if out.size > 1400 else out
'''


def test_the_numeric_check_would_notice_a_broken_stretch(tmp_path):
    """The trap has to be shown to close.

    Everything above trusts `dsp_numeric_check.py` to fail when the maths is
    wrong. Nothing proved that. A check that had itself broken - an import that
    stopped resolving, a `check()` that stopped counting failures, an exit code
    nobody read - would report success forever, and this file would stay green
    while the DSP rotted.

    So: run the same script against a deliberately broken COPY of _dsp.py in a
    throwaway tree, and require it to fail. Nothing real is touched. The break
    is the one this module already shipped once, so a green result here would
    mean the check cannot catch its own history.
    """
    exe = _numpy_interpreter()
    if exe is None:
        pytest.skip("no numpy-capable interpreter; see the test above")

    pkg = tmp_path / "tts" / "worker"
    pkg.mkdir(parents=True)
    (tmp_path / "tts" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "_dsp.py").write_text(
        WORKER_DSP.read_text(encoding="utf-8") + _TAIL_BUG, encoding="utf-8")
    # The script resolves the package from its OWN parents[1], so the copy has
    # to sit in a `tests/` folder of the throwaway tree to win the import.
    (tmp_path / "tests").mkdir()
    broken_check = tmp_path / "tests" / NUMERIC.name
    broken_check.write_text(NUMERIC.read_text(encoding="utf-8"),
                            encoding="utf-8")

    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run([exe, str(broken_check)], capture_output=True,
                          text=True, timeout=600, env=env)
    assert proc.returncode != 0, (
        "the numeric check passed a DSP that returns short - it is not "
        "checking anything:\n" + proc.stdout)
    assert "the output is the length the rate implies" in proc.stdout
    assert "all dsp numeric checks passed" not in proc.stdout
