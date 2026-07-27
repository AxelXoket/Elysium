"""matrix.py - every setting every engine has, and why one is greyed out.

A settings panel that only shows what the SELECTED engine supports quietly
teaches the wrong thing: a control appears, then disappears when the model is
swapped, and nobody learns that the difference belongs to the engine rather
than to the app. So the panel shows the union - and the ones that cannot do
anything here are visibly present, disabled, WITH THE REASON.

The reason is the whole point, and it is why this is not a one-line
set-difference. Three genuinely different things look identical from outside:

  unsupported  - this engine has no such control at all.
  dead         - the engine ACCEPTS the parameter and never applies it. Fish's
                 `repetition_penalty` is exactly this: declared at every layer,
                 never reaching the logits, with the real repetition control
                 hardcoded elsewhere. Calling that "unsupported" would be a lie
                 in the direction of making us look tidy.
  app_level    - the app implements it regardless of the engine (reading speed
                 is time-stretched when the engine has no rate control), so it
                 stays editable everywhere.

Host half, pure stdlib: this only reads ParamSpecs, so the whole matrix is
unit-testable with no engine, GPU or model folder anywhere.
"""
from __future__ import annotations

from typing import Any

from . import speed
from .base import DetectedModel, ParamSpec
from .registry import all_adapters

#: Parameters an engine declares and then ignores. Keyed by engine id; the
#: value is the sentence the panel shows. Each one is a claim about that
#: engine's SOURCE, established by reading it - not a guess from behaviour.
DEAD_PARAMS: dict[str, dict[str, str]] = {
    "fish_s2": {
        "repetition_penalty":
            "This engine accepts the value and never applies it - its "
            "repetition control is fixed internally, so the dial would do "
            "nothing.",
    },
}

#: Implemented by the app, so it works on every engine - and therefore NOT
#: saved with the model's own settings. Listed here so the panel can show that
#: the setting exists and say where it lives; editing it in this panel would
#: hand it to a save path that only knows engine parameters, which drops
#: unknown keys SILENTLY. A slider that moves and changes nothing is the exact
#: failure this whole matrix was built to make impossible.
APP_LEVEL: dict[str, str] = {
    "speed": "Set under Delivery - it is applied by Elysium and works the "
             "same on every voice model.",
}

#: Order groups appear in. Anything unlisted sorts after, alphabetically, so a
#: new group from a future adapter still lands somewhere sensible.
_GROUP_ORDER = ("voice", "quality", "limits", "general")


def _sort_key(spec: ParamSpec) -> tuple[int, int, str]:
    group = _GROUP_ORDER.index(spec.group) if spec.group in _GROUP_ORDER else 99
    return (group, 1 if spec.advanced else 0, spec.name)


def _nominal(engine_id: str) -> DetectedModel:
    """A stand-in model, only ever used to ask an adapter WHAT KNOBS IT HAS.

    `describe_settings` takes a model because some answers depend on the files
    (XTTS reads its language list from the model's own config; Chatterbox only
    offers `language_id` on the multilingual variant). The union needs the
    shape, not the values, so a model with no path on disk is exactly right:
    adapters that read files fall back to their own defaults, which is the
    superset a panel should show.

    `variant="multilingual"` is deliberate rather than arbitrary - asking for
    the SMALLER variant would hide a real parameter from the matrix, which is
    the one thing the matrix exists to stop happening.
    """
    return DetectedModel(
        uid=f"__matrix__{engine_id}",
        engine_id=engine_id,
        name="matrix probe",
        path="",
        variant="multilingual",
    )


def union_specs() -> list[ParamSpec]:
    """One ParamSpec per name across every registered engine.

    First writer wins, and the adapters are visited in priority order, so the
    label and help text a shared parameter shows come from the engine most
    likely to be selected rather than from whichever module imported first.
    """
    seen: dict[str, ParamSpec] = {}
    for adapter in sorted(all_adapters(), key=lambda a: a.priority):
        try:
            specs = adapter.describe_settings(_nominal(adapter.engine_id))
        except Exception:                                # noqa: BLE001
            # A descriptor that cannot be built without real files cannot
            # contribute. That is a gap in the matrix, never a broken panel.
            continue
        for spec in specs:
            seen.setdefault(spec.name, spec)
    return sorted(seen.values(), key=_sort_key)


def describe(engine_id: str, specs: list[ParamSpec]) -> list[dict[str, Any]]:
    """The union, annotated for ONE engine.

    `specs` is that engine's own descriptor - passed in rather than re-derived
    because building it needs the model folder, which this module deliberately
    knows nothing about.
    """
    own = {s.name: s for s in specs}
    dead = DEAD_PARAMS.get(engine_id, {})
    native_rate = speed.native_spec(specs)

    rows: list[dict[str, Any]] = []
    for spec in _merged(own, union_specs()):
        name = spec.name
        if name in APP_LEVEL:
            # Shown, not operable HERE. The one dial the user turns lives with
            # the other delivery preferences; an engine's own rate knob is
            # driven behind it (see tts/speed.py).
            rows.append(_row(spec, False, "app_level", APP_LEVEL[name],
                             native=bool(native_rate)))
            continue
        if native_rate is not None and name == native_rate.name:
            # Hidden, not disabled: it is not unavailable, it is already being
            # driven by the row above. Two controls for one behaviour is how a
            # settings page starts lying to the person reading it.
            continue
        if name in dead:
            rows.append(_row(spec, False, "dead", dead[name]))
            continue
        if name in own:
            rows.append(_row(own[name], True, "supported", ""))
            continue
        rows.append(_row(spec, False, "unsupported",
                         "The selected voice model has no such setting."))
    return rows


def _merged(own: dict[str, ParamSpec], union: list[ParamSpec]) -> list[ParamSpec]:
    """The union, plus anything only this engine has (a new adapter's own
    knobs must not vanish just because no one else declares them)."""
    names = {s.name for s in union}
    extra = [s for s in own.values() if s.name not in names]
    return sorted(union + extra, key=_sort_key)


def _row(spec: ParamSpec, editable: bool, status: str, reason: str,
         *, native: bool = False) -> dict[str, Any]:
    row = spec.to_json()
    row["editable"] = editable
    row["status"] = status
    row["reason"] = reason
    if status == "app_level":
        row["minimum"] = speed.MIN_RATE
        row["maximum"] = speed.MAX_RATE
        row["default"] = speed.DEFAULT_RATE
        # Honest about HOW it is done, because the two paths sound different:
        # a native rate is generated at that pace, a stretched one is reshaped.
        row["implemented_by"] = "engine" if native else "elysium"
    return row
