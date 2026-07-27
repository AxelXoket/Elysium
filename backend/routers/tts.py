"""routers/tts.py -- Voice model discovery + per-model settings (Phase M).

Routes:
    GET    /tts/models                  - detected models + unrecognized folders
    POST   /tts/rescan                  - force a fresh scan
    GET    /tts/models/{uid}/schema     - that model's own settings descriptor
    GET    /tts/models/{uid}/settings   - effective values (defaults + saved)
    POST   /tts/models/{uid}/settings   - validate + clamp + persist
    DELETE /tts/models/{uid}/settings   - reset to defaults
    GET    /tts/models/{uid}/readiness  - can it run right now, and if not why
    POST   /tts/models/{uid}/engine     - manual engine override (sidecar)
    GET    /tts/active                  - selected model + state
    POST   /tts/active                  - select a model (does NOT load it)
    POST   /tts/preflight               - will it fit right now, with these values
    GET    /tts/runtimes                - per-engine runtime state (app-owned)

Privacy invariants:
    - Voice runs entirely on this machine; this module makes NO network calls.
    - It never imports torch or any engine library (host half only), so it can
      answer on a machine with no GPU.
    - Model paths are local filesystem paths the user chose; nothing here reads
      weights, and nothing is uploaded anywhere.
    - Per-model VALUES live in the encrypted settings table. Only the engine
      identity may sit beside the weights as a plaintext sidecar, so a model
      folder stays self-describing when it is moved or the app is reinstalled.

Load/unload are deliberately absent: they need the worker half and land with it,
so nothing here can spawn a process. Preflight only READS the GPU (via nvidia-smi
in a subprocess) - it never allocates, so a fit check is always safe to call.

Settings and READINESS are deliberately separate answers. A model is always
inspectable - its page opens with no GPU, nothing installed and a half-finished
download - because hiding it teaches the user nothing. What is not allowed is
tuning a model that cannot speak and only discovering it at playback, so every
model payload carries a verdict listing every blocker at once.

The runtime registry is APP-OWNED: the user never edits runtimes.json by hand.
GET /tts/runtimes exists so Settings can offer a one-click setup and say plainly
when an interpreter was recorded but has since gone missing.
"""

import json
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from database import get_db, get_setting
from tts import adapter_for, check_fit, scan_roots
from tts import readiness as tts_readiness
from tts import runtimes as tts_runtimes
from tts.registry import all_adapters
from tts.base import DetectedModel
from tts.errors import (
    TTS_ENGINE_UNKNOWN,
    TTS_MODEL_INCOMPLETE,
    TTS_MODEL_UNKNOWN,
    TTS_PARAM_INVALID,
    TTS_SIDECAR_WRITE_FAILED,
    TTS_VALUES_TOO_LARGE,
    ParamError,
)
from tts.registry import SIDECAR_NAME

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tts", tags=["tts"])

SETTING_ACTIVE_UID = "tts_active_uid"
_MAX_VALUES_BYTES = 8 * 1024      # a settings map is tiny; anything bigger is abuse


def _settings_key(uid: str) -> str:
    return f"tts_model:{uid}"


def _find(uid: str) -> DetectedModel:
    """Resolve a uid against a fresh scan, or fail with the contract code."""
    for model in scan_roots().models:
        if model.uid == uid:
            return model
    raise HTTPException(400, TTS_MODEL_UNKNOWN)


def _adapter(model: DetectedModel):
    adapter = adapter_for(model.engine_id)
    if adapter is None:
        raise HTTPException(400, TTS_ENGINE_UNKNOWN)
    return adapter


def _saved_values(uid: str) -> dict:
    """Stored overrides for a model. A corrupt row degrades to {} - a mangled
    settings blob must never take voice down, let alone 500 the endpoint."""
    raw = get_setting(_settings_key(uid))
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("tts: ignoring corrupt settings row for uid=%s", uid)
        return {}
    return data if isinstance(data, dict) else {}


def _effective(model: DetectedModel) -> tuple[dict, dict]:
    """(values, source_map) - defaults overlaid with validated saved values."""
    adapter = _adapter(model)
    try:
        specs = adapter.describe_settings(model)
    except Exception:
        logger.warning("tts: descriptor failed for uid=%s", model.uid, exc_info=True)
        raise HTTPException(422, TTS_MODEL_INCOMPLETE)
    values = {s.name: s.default for s in specs}
    source = {s.name: "default" for s in specs}
    for key, val in _saved_values(model.uid).items():
        spec = next((s for s in specs if s.name == key), None)
        if spec is None:
            continue                      # spec changed under a saved value
        try:
            values[key] = spec.clamp(val)
            source[key] = "saved"
        except ParamError:
            logger.warning("tts: dropping unusable saved value uid=%s key=%s",
                           model.uid, key)
    return values, source


# ── models ───────────────────────────────────────────────────────────────────

def _values_for(model: DetectedModel) -> dict:
    """Saved values for the estimate, degrading to {} - a readiness verdict must
    never be the thing that fails."""
    try:
        return _effective(model)[0]
    except Exception:
        return {}


def _scan_payload(language: str | None = None) -> dict:
    result = scan_roots()
    verdicts = tts_readiness.evaluate_all(
        result.models, language=language, values_for=_values_for
    )
    return {
        # Every row carries its own verdict, so the list can badge "will not run
        # yet" without the UI making N extra calls - and so a model is never
        # presented as usable when it is not.
        "models": [
            {**m.to_json(), "readiness": verdicts[m.uid].to_json()}
            for m in result.models
        ],
        "unrecognized": [u.to_json() for u in result.unrecognized],
        "roots": result.roots,
        # A short list that stopped at the cap must not look like a complete
        # one. See ScanResult.truncated.
        "truncated": result.truncated,
    }


@router.get("/models")
def list_models(language: str | None = None) -> dict:
    return _scan_payload(language)


@router.post("/rescan")
def rescan(language: str | None = None) -> dict:
    # A rescan can re-identify a folder (sidecar added, files completed), so
    # the uid -> tag-capability memo must not outlive it.
    import voice_tags

    voice_tags.reset_tag_support_cache()
    return _scan_payload(language)


@router.get("/models/{uid}/readiness")
def model_readiness(uid: str, language: str | None = None) -> dict:
    """Can this model speak right now - and if not, every reason at once.

    Settings stay available whatever this says. The two answers are deliberately
    separate: taking the page away would teach the user nothing, while showing
    it without this verdict would let them tune a model that cannot run.
    """
    model = _find(uid)
    return tts_readiness.evaluate(
        model, _values_for(model), language=language
    ).to_json()


@router.get("/models/{uid}/schema")
def model_schema(uid: str) -> dict:
    model = _find(uid)
    adapter = _adapter(model)
    try:
        params = [s.to_json() for s in adapter.describe_settings(model)]
    except Exception:
        # A descriptor that cannot be built from the model's files is a broken
        # model folder - a coded 422 the UI can phrase, never a bare 500.
        logger.warning("tts: descriptor failed for uid=%s", uid, exc_info=True)
        raise HTTPException(422, TTS_MODEL_INCOMPLETE)
    # `params` stays exactly what it was - this model's own knobs, which is
    # what save/validate work from. `matrix` is the panel's view: every knob
    # every engine has, with the ones that cannot do anything here marked and
    # explained. Two fields rather than one because they answer two different
    # questions, and merging them would make the save path guess.
    try:
        from tts import matrix as tts_matrix

        matrix_rows = tts_matrix.describe(model.engine_id,
                                          adapter.describe_settings(model))
    except Exception:                                    # noqa: BLE001
        # The matrix is an explanation, not a requirement. Losing it must not
        # cost somebody the settings page.
        logger.warning("tts: matrix failed for uid=%s", uid, exc_info=True)
        matrix_rows = []
    return {
        "uid": model.uid,
        "engine_id": model.engine_id,
        "display_name": adapter.display_name,
        "variant": model.variant,
        "capabilities": adapter.capabilities.to_json(),
        "params": params,
        "matrix": matrix_rows,
    }


# ── per-model settings ───────────────────────────────────────────────────────

class ValuesBody(BaseModel):
    values: dict = {}


def _reject_oversized(values: dict) -> None:
    """Size-check in the HANDLER, not a pydantic validator: a validator raises
    422 with pydantic's own body, but this API's contract is that the response
    detail IS the error code (the frontend maps it by exact string)."""
    try:
        size = len(json.dumps(values))
    except (TypeError, ValueError):
        raise HTTPException(400, TTS_PARAM_INVALID)
    if size > _MAX_VALUES_BYTES:
        raise HTTPException(400, TTS_VALUES_TOO_LARGE)


@router.get("/models/{uid}/settings")
def get_model_settings(uid: str) -> dict:
    model = _find(uid)
    values, source = _effective(model)
    return {"uid": uid, "values": values, "source_map": source}


@router.post("/models/{uid}/settings")
def save_model_settings(uid: str, body: ValuesBody) -> dict:
    _reject_oversized(body.values)
    model = _find(uid)
    adapter = _adapter(model)
    try:
        cleaned = adapter.clamp_values(model, body.values)
    except ParamError:
        raise HTTPException(400, TTS_PARAM_INVALID)

    merged = {**_saved_values(uid), **cleaned}
    with get_db() as con:
        con.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_settings_key(uid), json.dumps(merged)),
        )
    values, source = _effective(model)
    return {"uid": uid, "values": values, "source_map": source}


@router.delete("/models/{uid}/settings")
def reset_model_settings(uid: str) -> dict:
    model = _find(uid)
    with get_db() as con:
        con.execute("DELETE FROM settings WHERE key = ?", (_settings_key(uid),))
    values, source = _effective(model)
    return {"uid": uid, "values": values, "source_map": source}


# ── manual engine override ───────────────────────────────────────────────────

class EngineBody(BaseModel):
    engine_id: str


@router.post("/models/{uid}/engine")
def override_engine(uid: str, body: EngineBody) -> dict:
    """Write the sidecar so a mis-fingerprinted folder can be rescued.

    The sidecar is plaintext BESIDE THE WEIGHTS on purpose: it is not user data,
    and keeping it there means the folder still describes itself after a move or
    a reinstall.
    """
    if adapter_for(body.engine_id) is None:
        raise HTTPException(400, TTS_ENGINE_UNKNOWN)
    model = _find(uid)
    from pathlib import Path

    path = Path(model.path) / SIDECAR_NAME
    # This is the ONE place voice writes into a user-supplied folder, so it is
    # confined twice: the resolved target must stay inside a configured models
    # root, and an existing symlink there is refused rather than followed (a
    # link planted in a downloaded archive must not redirect our write).
    try:
        roots = [Path(r).resolve() for r in scan_roots().roots]
        target_dir = path.parent.resolve()
        if not any(target_dir == r or r in target_dir.parents for r in roots):
            raise HTTPException(400, TTS_MODEL_UNKNOWN)
        if path.is_symlink():
            logger.warning("tts: refusing to write sidecar through a symlink")
            raise HTTPException(400, TTS_MODEL_UNKNOWN)
    except OSError:
        raise HTTPException(400, TTS_MODEL_UNKNOWN)
    try:
        path.write_text(json.dumps({"engine_id": body.engine_id}), encoding="utf-8")
    except OSError:
        # The folder exists (we just scanned it) but is not writable - a
        # read-only drive or a permission problem. Telling the user to rescan
        # would send them after the wrong thing.
        logger.warning("tts: sidecar write failed for uid=%s", uid)
        # Its own code: "the voice engine could not start" (worker_failed)
        # would be a lie - no worker is anywhere near this endpoint. The real
        # story is a folder we may not write to (read-only drive, permissions).
        raise HTTPException(500, TTS_SIDECAR_WRITE_FAILED)
    import voice_tags

    voice_tags.reset_tag_support_cache()      # the engine identity just changed
    return _scan_payload()


# ── active selection ─────────────────────────────────────────────────────────

class ActiveBody(BaseModel):
    uid: str


def _any_engine_installed() -> bool:
    """Is at least one engine runtime registered AND still on disk?

    A cheap runtimes.json read - no model scan, no readiness evaluation, no
    VRAM probe - because it rides on /tts/active, which the chat polls.

    The chat needs it to tell two very different states apart. With no engine
    at all, showing nothing is right: there is nothing to offer. With an engine
    installed, a model downloaded and a reference voice recorded but nothing
    SELECTED, showing nothing is wrong - the user did every part of the setup
    and their chat looks identical to a fresh install.
    """
    from tts.registry import all_adapters

    return any(
        tts_runtimes.status(adapter.engine_id).state == "ready"
        for adapter in all_adapters()
    )


@router.get("/active")
def get_active(language: str | None = None) -> dict:
    uid = get_setting(SETTING_ACTIVE_UID)
    if not uid:
        return {"uid": None, "state": "unloaded", "engine_id": None,
                "vram_mb": None, "error_code": None, "readiness": None,
                "voice_installed": _any_engine_installed()}
    model = next((m for m in scan_roots().models if m.uid == uid), None)
    if model is None:
        # The folder was renamed or deleted while selected. Report it; a silent
        # empty state would look like the setting simply did not stick.
        return {"uid": uid, "state": "error", "engine_id": None,
                "vram_mb": None, "error_code": TTS_MODEL_UNKNOWN,
                "readiness": None, "voice_installed": True}
    # The SELECTED model is where an unusable state matters most - this is the
    # one the user expects to hear. `error_code` carries the first blocker so a
    # caller that only reads that field still learns voice will not work.
    verdict = tts_readiness.evaluate(model, _values_for(model), language=language)
    blocker = next((i.code for i in verdict.issues if i.severity == tts_readiness.BLOCKER), None)
    # The LIVE state comes from the host: a hardcoded "unloaded" here would
    # keep saying so while a model is resident - or worse, while its worker
    # just crashed, which is exactly the moment this endpoint must not lie.
    from tts.host import get_host

    snap = get_host().snapshot()
    if snap["uid"] == uid or snap["state"] == "error":
        return {"uid": uid, "state": snap["state"],
                "engine_id": model.engine_id,
                "vram_mb": snap["vram_mb"],
                "error_code": snap["error_code"] or blocker,
                "readiness": verdict.to_json(), "voice_installed": True}
    return {"uid": uid, "state": "unloaded", "engine_id": model.engine_id,
            "vram_mb": None, "error_code": blocker,
            "readiness": verdict.to_json(), "voice_installed": True}


@router.post("/active")
def set_active(body: ActiveBody) -> dict:
    model = _find(body.uid)
    with get_db() as con:
        con.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (SETTING_ACTIVE_UID, model.uid),
        )
    return get_active()


# ── preflight + runtime status ───────────────────────────────────────────────

class PreflightBody(BaseModel):
    uid: str
    values: dict = {}


@router.post("/preflight")
def preflight(body: PreflightBody) -> dict:
    """Will this model fit RIGHT NOW, with these settings?

    POST rather than GET-with-query because the answer depends on the whole
    values map, and this project keeps meaningful data out of query strings.
    Answering honestly here is what stops a load from filling the card and
    dragging the user's whole machine down.
    """
    _reject_oversized(body.values)
    model = _find(body.uid)
    adapter = _adapter(model)
    try:
        values = adapter.clamp_values(model, body.values)
    except ParamError:
        raise HTTPException(400, TTS_PARAM_INVALID)
    return check_fit(model, values).to_json()


@router.get("/runtimes")
def list_runtimes() -> dict:
    """Per-engine runtime state, so the UI can offer "Set up voice" for the
    engine the user actually has a model for - and say plainly when one is
    recorded but its interpreter has gone missing."""
    engines = [a.engine_id for a in all_adapters()]
    return {
        "runtimes": [s.to_json() for s in tts_runtimes.all_status(engines)],
        "engines": [
            {"engine_id": a.engine_id, "display_name": a.display_name}
            for a in all_adapters()
        ],
    }
