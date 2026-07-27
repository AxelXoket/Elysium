"""speed.py - one reading-speed dial, two ways of honouring it.

The engines disagree about whether speaking rate is theirs to control. XTTS has
a native `speed` parameter; Fish S2 and Chatterbox have none. Exposing that
difference to the user would mean a dial that appears and disappears depending
on which voice model is selected - and on XTTS, TWO dials doing the same job,
because the app-level one would still be there.

So there is exactly one dial, and this module decides who implements it:

    native  - the engine has its own `speed`; it is driven and NO DSP runs.
              Always better when available: the engine generates at that pace
              rather than having its output reshaped afterwards, so there is
              nothing to smear.
    dsp     - the engine has no rate control; the rendered audio is time
              stretched worker-side (see tts/worker/_dsp.py).

Host half, pure stdlib: this only reads ParamSpecs and returns a plan, so the
routing is unit-testable without an engine, a GPU or numpy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .base import ParamSpec

#: The user-facing dial. Deliberately narrower than XTTS's own 0.5-1.5: past
#: these bounds the DSP path smears transients, and a dial whose usable range
#: depends on the selected engine is not one dial, it is two wearing a hat.
MIN_RATE = 0.80
MAX_RATE = 1.25
DEFAULT_RATE = 1.0

#: Names an engine may use for its own rate control.
_NATIVE_NAMES = ("speed", "speaking_rate", "length_scale")

#: Below this the difference is not audible and not worth a pass over the audio.
_EPSILON = 0.02


@dataclass(frozen=True)
class SpeedPlan:
    """Who applies the rate, and what each side should be told."""
    rate: float
    native_param: str | None
    dsp_rate: float

    @property
    def uses_dsp(self) -> bool:
        return self.native_param is None and abs(self.dsp_rate - 1.0) >= _EPSILON


def clamp(rate: float | None) -> float:
    if rate is None:
        return DEFAULT_RATE
    try:
        value = float(rate)
    except (TypeError, ValueError):
        return DEFAULT_RATE
    if value != value:                                  # NaN
        return DEFAULT_RATE
    return max(MIN_RATE, min(MAX_RATE, value))


def native_spec(specs: Iterable[ParamSpec]) -> ParamSpec | None:
    """The engine's own rate knob, if it has one."""
    by_name = {s.name: s for s in specs}
    for name in _NATIVE_NAMES:
        if name in by_name:
            return by_name[name]
    return None


def plan(specs: Sequence[ParamSpec], rate: float | None) -> SpeedPlan:
    """Decide how this engine will honour the requested rate."""
    wanted = clamp(rate)
    spec = native_spec(specs)
    if spec is None:
        return SpeedPlan(rate=wanted, native_param=None, dsp_rate=wanted)
    # The engine's own range may be narrower than ours; its clamp wins, because
    # sending a value it will reject or silently floor helps nobody.
    return SpeedPlan(rate=wanted,
                     native_param=spec.name,
                     dsp_rate=DEFAULT_RATE)


def hide_native(specs: Sequence[ParamSpec]) -> list[ParamSpec]:
    """The list the settings panel should show.

    The engine's raw rate knob is removed because the shared dial already
    drives it. Two controls for one behaviour is how a settings page starts
    lying to the person reading it.
    """
    spec = native_spec(specs)
    if spec is None:
        return list(specs)
    return [s for s in specs if s.name != spec.name]


def engine_values(specs: Sequence[ParamSpec], rate: float | None) -> dict:
    """What to merge into the engine's parameter values for this rate."""
    p = plan(specs, rate)
    if p.native_param is None:
        return {}
    spec = native_spec(specs)
    assert spec is not None
    return {p.native_param: spec.clamp(p.rate)}
