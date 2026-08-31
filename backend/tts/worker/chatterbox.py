# -*- coding: utf-8 -*-
"""tts/worker/chatterbox.py - Resemble Chatterbox, the ENGINE half.

Runs in the chatterbox venv's own interpreter, spawned as

    <chatterbox venv python.exe> -u <this file>

so it may import torch and chatterbox, and may NOT import anything from the
Elysium app. `_wire` is the one exception: it is stdlib-only and sits next to
this file, which is on sys.path because the script was run by path.

WHAT WAS READ RATHER THAN GUESSED
    chatterbox_tts 0.1.7, installed at
    tts_bakeoff/chatterbox_env/Lib/site-packages/chatterbox/

    ChatterboxMultilingualTTS.generate(text, language_id, audio_prompt_path=None,
        exaggeration=0.5, cfg_weight=0.5, temperature=0.8,
        repetition_penalty=2.0, min_p=0.05, top_p=1.0)
    ChatterboxTTS.generate(text, repetition_penalty=1.2, min_p=0.05, top_p=1.0,
        audio_prompt_path=None, exaggeration=0.5, cfg_weight=0.5,
        temperature=0.8)

    There is NO top_k and NO speed on either one - passing either raises
    TypeError, which is why the host descriptor in adapters/chatterbox.py does
    not offer them and why the kwargs below are built from an explicit list
    instead of from **values.

    language_id is positional-required on the multilingual build and is checked
    against a 23-entry SUPPORTED_LANGUAGES dict (ValueError on a miss). The
    english build has no language argument at all - hence the variant branch.

    Output is S3GEN_SR = 24000 Hz, mono, float in [-1, 1], shape (1, N).

    Cloning is by `audio_prompt_path` only; no transcript is ever consulted.
    It is implemented here straight from the real API - prepare_conditionals()
    is the same call generate() makes internally. The bake-off driver scripts
    under tts_bakeoff/scripts only ever used the built-in conds.pt voice, but
    this worker's clone path has since been driven end to end against the real
    checkpoint (load -> clone from refs/ref1.wav -> back to the built-in
    voice), so it produces audio. Nobody has sat down and LISTENED to a clone
    yet; that judgement is still outstanding.

STATEFULNESS - DO NOT MAKE THIS CONCURRENT
    The model is not a pure function. generate() rebuilds `self.conds.t3` in
    place whenever `exaggeration` differs from the current conditioning, and
    prepare_conditionals() replaces `self.conds` wholesale. Two requests in
    flight at once would speak each other's voice, or crash mid-rebuild.
    _wire.serve() is a single-threaded read-one-frame/answer-one-frame loop and
    this file relies on that. If anyone ever wants throughput here, the answer
    is a second worker process, never a thread.
"""
from __future__ import annotations

import os
import sys
import traceback
import wave
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
sys.path.insert(0, _HERE)
import _wire  # noqa: E402  (deliberate: sys.path is set up on the line above)
import _dsp    # noqa: E402  (sibling DSP; numpy is imported inside it)


def _unshadow() -> None:
    """Stop this file from being mistaken for the engine it drives.

    MEASURED, not theoretical: this script is named chatterbox.py and lives in
    the directory Python itself puts at sys.path[0] for a script run by path.
    So `from chatterbox.tts import ChatterboxTTS` resolves to THIS FILE and dies

        ModuleNotFoundError: No module named 'chatterbox.tts';
                             'chatterbox' is not a package

    which would have looked exactly like a broken venv (exit 3) forever. Once
    _wire is imported we no longer need to outrank anything, so our directory
    moves to the BACK of sys.path where site-packages wins. An entry of "" is
    cwd, hence the abspath before comparing.
    """
    here = os.path.normcase(_HERE)
    kept = [p for p in sys.path
            if os.path.normcase(os.path.abspath(p or ".")) != here]
    kept.append(_HERE)
    sys.path[:] = kept


_unshadow()

# ── rule 7: nothing here may reach the network ───────────────────────────────
# MTLTokenizer builds a ChineseCangjieConverter in __init__, which calls
# hf_hub_download("Cangjie5_TC.json") unconditionally. It swallows its own
# failure, so offline mode turns that from a silent download attempt into a
# logged warning (Chinese loses Cangjie segmentation; every other language is
# unaffected). Forced, not setdefault - an inherited env must not re-enable it.
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_DATASETS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

SAMPLE_RATE = 24000          # S3GEN_SR, fixed by the vocoder

# Defaults MUST match ParamSpec-by-ParamSpec with
# backend/tts/adapters/chatterbox.py - the settings UI is generated from that
# file, so a name typo here is a knob that silently does nothing.
#   exaggeration 0.5 | cfg_weight 0.5 | temperature 0.8
#   repetition_penalty 2.0 | min_p 0.05 | top_p 1.0 | language_id "en"
# (The english class's own signature default for repetition_penalty is 1.2, but
# the descriptor says 2.0 and the descriptor is what the user sees; we send the
# descriptor's value explicitly so the slider never lies.)
DEFAULTS = {
    "exaggeration": 0.5,
    "cfg_weight": 0.5,
    "temperature": 0.8,
    "repetition_penalty": 2.0,
    "min_p": 0.05,
    "top_p": 1.0,
}
GEN_FLOAT_PARAMS = tuple(DEFAULTS)      # exactly the kwargs generate() accepts
DEFAULT_LANGUAGE = "en"

# What from_local() opens, per variant. Checked before we call it so an
# incomplete download names the missing file instead of raising a bare
# FileNotFoundError out of library code.
REQUIRED_FILES = {
    "multilingual": ("ve.pt", "t3_mtl23ls_v2.safetensors", "s3gen.pt",
                     "grapheme_mtl_merged_expanded_v1.json"),
    "english": ("ve.safetensors", "t3_cfg.safetensors", "s3gen.safetensors",
                "tokenizer.json"),
}
# Same markers the host adapter fingerprints with, so a request that forgot to
# carry `variant` reaches the same verdict the app already showed the user.
MTL_MARKERS = ("grapheme_mtl_merged_expanded_v1.json", "t3_mtl23ls_v2.safetensors")

MIN_REF_SECONDS = 0.5
MAX_REF_SECONDS = 30.0       # the model reads 10 s (decoder) / 6 s (encoder)
REF_SILENCE_PEAK = 1e-4

STATE = {
    "model": None,
    "key": None,             # (model_path, variant, device) currently loaded
    "variant": None,
    "device": None,
    "builtin_conds": None,   # conds.pt voice, kept so cloning is reversible
    "ref_key": None,         # (path, mtime, size) the conditioning was built from
}
ENGINE: dict = {}            # torch / numpy / classes, filled by _ensure_engine


# ── engine import ────────────────────────────────────────────────────────────

def _ensure_engine(variant: str | None = None) -> None:
    """Import torch + chatterbox, or exit 3 = "the install is damaged".

    Everything heavy is imported here rather than at module level so that a
    broken venv produces EXIT_ENGINE_IMPORT (which the host turns into "set up
    voice again") instead of a nameless exit 1. `variant=None` asks only for
    torch/numpy - prepare_ref decodes audio and never needs a model class.
    """
    need = {"multilingual": "mtl", "english": "en"}.get(variant or "")
    if ENGINE.get("torch") is not None and (need is None or ENGINE.get(need)):
        return
    try:
        import numpy
        import torch

        ENGINE["torch"] = torch
        ENGINE["numpy"] = numpy
        if need == "mtl" and ENGINE.get("mtl") is None:
            _patch_perth()
            from chatterbox.mtl_tts import SUPPORTED_LANGUAGES, ChatterboxMultilingualTTS
            ENGINE["mtl"] = ChatterboxMultilingualTTS
            ENGINE["languages"] = dict(SUPPORTED_LANGUAGES)
        elif need == "en" and ENGINE.get("en") is None:
            _patch_perth()
            from chatterbox.tts import ChatterboxTTS
            ENGINE["en"] = ChatterboxTTS
    except Exception:                           # noqa: BLE001 - then leave
        traceback.print_exc()
        print("chatterbox: engine import failed - the venv is damaged",
              file=sys.stderr, flush=True)
        sys.exit(_wire.EXIT_ENGINE_IMPORT)


def _patch_perth() -> None:
    """Neutralise a null PerTh watermarker before chatterbox touches it.

    perth/__init__.py sets PerthImplicitWatermarker = None when its optional
    import fails, and both TTS classes call perth.PerthImplicitWatermarker() in
    __init__ - so a partial perth install becomes "TypeError: 'NoneType' object
    is not callable" at load time. Observed in the bake-off; the driver scripts
    carry the same guard. Watermarking is not something this app needs.
    """
    class _NoWatermark:
        def apply_watermark(self, wav, sample_rate=None, **kw):
            return wav

        def get_watermark(self, *a, **k):
            return None

    try:
        import perth
    except Exception:                           # noqa: BLE001 - stub it whole
        import types
        perth = types.ModuleType("perth")
        sys.modules["perth"] = perth
    if getattr(perth, "PerthImplicitWatermarker", None) is None:
        perth.PerthImplicitWatermarker = _NoWatermark
    if getattr(perth, "DummyWatermarker", None) is None:
        perth.DummyWatermarker = _NoWatermark


# ── small helpers ────────────────────────────────────────────────────────────

def _fail(code: str, detail: str) -> _wire.WorkerError:
    return _wire.WorkerError(code, detail)


def _die_model_load(detail: str) -> None:
    """Exit 4: the environment is fine, THIS model would not load."""
    print(f"chatterbox: {detail}", file=sys.stderr, flush=True)
    sys.exit(_wire.EXIT_MODEL_LOAD)


def _float(values: dict, name: str) -> float:
    """One tunable, with the descriptor's default when absent or unusable.

    The host clamps against the ParamSpec before it gets here; this is the
    belt-and-braces pass so a hand-written frame cannot put a string into a
    kwarg that ends up inside a CUDA kernel.
    """
    raw = values.get(name, DEFAULTS[name])
    try:
        out = float(raw)
    except (TypeError, ValueError):
        return float(DEFAULTS[name])
    if out != out or out in (float("inf"), float("-inf")):
        return float(DEFAULTS[name])
    return out


def _variant_of(req: dict, model_dir: Path) -> str:
    """multilingual | english. The host normally says; the directory decides
    when it does not, using the adapter's own markers."""
    said = (req.get("variant") or "").strip().lower()
    if said in ("multilingual", "english"):
        return said
    for marker in MTL_MARKERS:
        if (model_dir / marker).is_file():
            return "multilingual"
    return "english"


def _device(req: dict) -> str:
    """cuda when we have it, cpu otherwise. A request that asks for cuda on a
    machine without it gets cpu rather than a CUDA-not-available crash."""
    torch = ENGINE["torch"]
    has_cuda = bool(torch.cuda.is_available())
    want = (req.get("device") or "").strip().lower()
    if want == "cpu":
        return "cpu"
    if want == "cuda":
        return "cuda" if has_cuda else "cpu"
    return "cuda" if has_cuda else "cpu"


def _model_dir(req: dict) -> Path:
    raw = req.get("model_path") or req.get("model_dir") or ""
    if not raw:
        raise _fail(_wire.CODE_WORKER_FAILED, "no model_path in request")
    path = Path(str(raw)).expanduser()
    try:
        path = path.resolve()
    except OSError:
        pass
    if not path.is_dir():
        _die_model_load(f"model directory does not exist: {path}")
    return path


def _reference_path(req: dict, values: dict) -> Path | None:
    """The clip to clone from, if any.

    `reference_voice` is the ParamSpec name (type voice_ref). A prepare_ref
    round trip hands back a normalised wav, so an explicit req["reference"]
    wins over the raw descriptor value when the host chooses to use one.
    """
    raw = (req.get("reference") or req.get("reference_path")
           or values.get("reference_voice", "") or "")
    raw = str(raw).strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_file():
        raise _fail(_wire.CODE_REFERENCE_INVALID,
                    f"reference clip not found: {path}")
    return path


def _ref_key(path: Path):
    try:
        st = path.stat()
        return (str(path), int(st.st_mtime), int(st.st_size))
    except OSError as exc:
        raise _fail(_wire.CODE_REFERENCE_INVALID, f"cannot read reference: {exc}")


def _free_model() -> None:
    STATE.update(model=None, key=None, variant=None, device=None,
                 builtin_conds=None, ref_key=None)
    torch = ENGINE.get("torch")
    if torch is not None and torch.cuda.is_available():
        try:
            torch.cuda.empty_cache()
        except Exception:                       # noqa: BLE001 - best effort
            pass


def _write_wav(wav, out_path: Path) -> float:
    """(1, N) float tensor -> 16-bit PCM mono at 24 kHz. Returns seconds.

    Written with stdlib `wave` on purpose: torchaudio.save works in the bake-off
    venv but picks a backend at runtime, and a missing backend inside a shipped
    install would fail at the very last step of a successful synthesis.
    """
    np = ENGINE["numpy"]
    data = wav.detach().to("cpu").float().numpy() if hasattr(wav, "detach") else np.asarray(wav)
    data = np.squeeze(data)
    if data.ndim > 1:                           # (C, N) -> mono
        data = data.mean(axis=0)
    data = np.nan_to_num(data, nan=0.0, posinf=0.0, neginf=0.0)
    if data.size == 0:
        raise _fail(_wire.CODE_SYNTHESIS_FAILED, "the model returned no audio")
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    if peak > 1.0:                              # only touch it if it would clip
        data = data / peak
    pcm = np.clip(data, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(SAMPLE_RATE)
        fh.writeframes(pcm.tobytes())
    return round(pcm.size / float(SAMPLE_RATE), 3)


# ── ops ──────────────────────────────────────────────────────────────────────

def _op_load(req: dict, send) -> dict:
    model_dir = _model_dir(req)
    variant = _variant_of(req, model_dir)
    _ensure_engine(variant)
    device = _device(req)
    key = (str(model_dir), variant, device)

    if STATE["model"] is not None and STATE["key"] == key:
        return {"loaded": True, "cached": True, "variant": variant,
                "device": device, "sample_rate": SAMPLE_RATE}

    missing = [name for name in REQUIRED_FILES[variant]
               if not (model_dir / name).is_file()]
    if missing:
        _die_model_load(
            f"{variant} checkpoint is incomplete, missing: {', '.join(missing)}")

    # Drop the old one FIRST: loading a second set of weights while the first is
    # still resident is how a 3.8 GB model needs 7.6 GB of VRAM.
    if STATE["model"] is not None:
        _free_model()

    send(_wire.event("progress", stage="loading", pct=0.05))
    cls = ENGINE["mtl"] if variant == "multilingual" else ENGINE["en"]
    try:
        # from_local ONLY. from_pretrained() would snapshot_download() the
        # checkpoint from HuggingFace - the user already has these files.
        model = cls.from_local(model_dir, device)
    except Exception as exc:                    # noqa: BLE001
        if _wire.is_oom(exc):
            raise _wire.oom(str(exc))
        traceback.print_exc()
        _die_model_load(f"{type(exc).__name__}: {exc}")
        raise                                   # unreachable; keeps flow honest

    STATE.update(model=model, key=key, variant=variant, device=device,
                 builtin_conds=getattr(model, "conds", None), ref_key=None)
    send(_wire.event("progress", stage="loaded", pct=1.0))
    return {
        "loaded": True,
        "cached": False,
        "variant": variant,
        "device": device,
        "sample_rate": int(getattr(model, "sr", SAMPLE_RATE)),
        "has_builtin_voice": STATE["builtin_conds"] is not None,
    }


def _apply_reference(model, ref: Path | None, exaggeration: float) -> None:
    """Point the model at the voice this request wants.

    Conditioning is cached by (path, mtime, size) because building it runs the
    voice encoder and the speech tokenizer over the clip - real work we would
    otherwise repeat for every single line of dialogue. Passing
    audio_prompt_path to generate() does exactly this, every call.

    Going back to the built-in voice restores the conds.pt object captured at
    load time; prepare_conditionals() REPLACES model.conds rather than mutating
    it, so that original object is still intact and still on the right device.

    The restore is decided by OBJECT IDENTITY, not by `ref_key`. `ref_key`
    records what the cache was built from and is cleared on any failed clip and
    by prepare_ref, so it cannot answer "which voice is the model wearing right
    now". MEASURED with a fake model: keying the restore off `ref_key` let a
    previous clone survive into a built-in-voice request after a rejected clip
    (and again after a prepare_ref round trip) - the wrong person spoke and
    nothing anywhere reported an error. `is` against the object captured at
    load time is exact, because generate() only ever swaps conds.t3 in place
    while prepare_conditionals() replaces conds wholesale.
    """
    if ref is None:
        STATE["ref_key"] = None
        if STATE["builtin_conds"] is None:
            # Do not leave a stale clone behind to be used by accident: the
            # request asked for a voice this checkpoint does not carry.
            model.conds = None
            raise _fail(_wire.CODE_REFERENCE_INVALID,
                        "this checkpoint ships no built-in voice (conds.pt) - "
                        "choose a reference clip")
        if getattr(model, "conds", None) is not STATE["builtin_conds"]:
            model.conds = STATE["builtin_conds"]
        return

    key = _ref_key(ref)
    if STATE["ref_key"] == key:
        return
    try:
        # No transcript anywhere: Chatterbox clones from audio alone.
        model.prepare_conditionals(str(ref), exaggeration=exaggeration)
    except Exception as exc:                    # noqa: BLE001
        if _wire.is_oom(exc):
            raise _wire.oom(str(exc))
        # The half-built conditioning is not to be trusted; force a rebuild.
        STATE["ref_key"] = None
        raise _fail(_wire.CODE_REFERENCE_INVALID,
                    f"could not read the reference clip ({type(exc).__name__}: {exc})")
    STATE["ref_key"] = key


def _op_synthesize(req: dict, send) -> dict:
    if STATE["model"] is None:
        if not (req.get("model_path") or req.get("model_dir")):
            raise _fail(_wire.CODE_SYNTHESIS_FAILED, "no model is loaded")
        _op_load(req, send)                     # lazy load, same request frame
    model = STATE["model"]

    text = str(req.get("text") or "").strip()
    if not text:
        raise _fail(_wire.CODE_SYNTHESIS_FAILED, "nothing to say: text is empty")
    out_raw = req.get("out")
    if not out_raw:
        raise _fail(_wire.CODE_SYNTHESIS_FAILED, "no output path in request")
    out_path = Path(str(out_raw)).expanduser()

    values = req.get("values") or {}
    kwargs = {name: _float(values, name) for name in GEN_FLOAT_PARAMS}

    if STATE["variant"] == "multilingual":
        lang = str(values.get("language_id") or req.get("language_id")
                   or DEFAULT_LANGUAGE).strip().lower()
        # Validated here so a stale UI gets our sentence rather than the
        # library's ValueError arriving as an anonymous worker failure.
        if lang not in ENGINE.get("languages", {}):
            raise _fail(_wire.CODE_SYNTHESIS_FAILED,
                        f"this build cannot speak '{lang}'")
        kwargs["language_id"] = lang
    # The english build has no language argument at all - passing one is a
    # TypeError, so nothing is added in that branch.

    ref = _reference_path(req, values)
    send(_wire.event("progress", stage="conditioning", pct=0.1))
    _apply_reference(model, ref, kwargs["exaggeration"])

    send(_wire.event("progress", stage="synthesizing", pct=0.3))
    try:
        # One at a time. See the STATEFULNESS note at the top of this file.
        # Long inputs are capped by the model at max_new_tokens=1000 (~20 s of
        # speech); splitting a paragraph into sentences is the host's job.
        wav = model.generate(text, **kwargs)
    except Exception as exc:                    # noqa: BLE001
        if _wire.is_oom(exc):
            raise _wire.oom(str(exc))
        traceback.print_exc()
        raise _fail(_wire.CODE_SYNTHESIS_FAILED, f"{type(exc).__name__}: {exc}")

    wav = _retime(wav, req.get("rate"), send)
    send(_wire.event("progress", stage="writing", pct=0.9))
    seconds = _write_wav(wav, out_path)
    return {"path": str(out_path), "sample_rate": SAMPLE_RATE, "seconds": seconds}


def _retime(wav, rate, send):
    """Reading speed. Chatterbox has no rate knob of its own.

    So speed.plan() routes the dial down the DSP path and the host puts `rate`
    in the synthesize frame - which this worker simply never read. The dial
    moved and the voice did not change, while the settings matrix told the user
    the opposite ("applied by Elysium and works the same on every voice model").
    That is exactly the "slider that moves and changes nothing" the matrix
    exists to make impossible.

    WSOLA on the rendered waveform, like fish_s2: pitch is untouched because the
    sample rate never changes.
    """
    if _dsp.is_noop(rate):
        return wav
    np = ENGINE["numpy"]
    data = (wav.detach().to("cpu").float().numpy()
            if hasattr(wav, "detach") else np.asarray(wav))
    data = np.squeeze(data)
    if data.ndim > 1:                           # (C, N) -> mono
        data = data.mean(axis=0)
    try:
        out = _dsp.time_stretch(data, rate)
    except Exception as exc:                    # noqa: BLE001
        # Speaking at the wrong pace beats not speaking: a failed stretch must
        # not lose a sentence that generated perfectly well. But it is
        # REPORTED - a silently ignored dial is indistinguishable from a broken
        # one, which is the bug this whole change is about.
        # THE EXCEPTION GOES IN `detail`, NOT IN `note`.
        #
        # It used to be the note, and a note is echoed to the log verbatim -
        # so a stretch failure whose message quotes the sentence it could not
        # stretch wrote that sentence into elysium.log, in the clear, beside
        # the vault, during synthesis. `detail` is sanitized at the boundary
        # down to the exception's class name, which is the part that helps.
        send(_wire.event("progress", stage="retime_failed", pct=0.9,
                         note=_wire.NOTE_RETIME_FAILED,
                         detail=f"{type(exc).__name__}: {exc}"))
        return wav
    send(_wire.event("progress", stage="retimed", pct=0.9,
                     rate=round(_dsp.clamp_rate(rate), 3)))
    return out


def _op_prepare_ref(req: dict, send) -> dict:
    """Decode any clip the user picked into the mono 24 kHz wav we clone from.

    Done here rather than in the app because librosa (and therefore mp3/m4a
    decoding) lives in this venv. Nothing about it needs the model loaded.
    """
    values = req.get("values") or {}
    raw = (req.get("path") or req.get("reference") or req.get("reference_path")
           or values.get("reference_voice", "") or "")
    if not str(raw).strip():
        raise _fail(_wire.CODE_REFERENCE_INVALID, "no reference clip given")
    src = Path(str(raw).strip()).expanduser()
    if not src.is_file():
        raise _fail(_wire.CODE_REFERENCE_INVALID, f"no such file: {src}")

    _ensure_engine()                            # numpy only; no model needed
    np = ENGINE["numpy"]
    try:
        import librosa                          # a chatterbox dependency
        audio, _sr = librosa.load(str(src), sr=SAMPLE_RATE, mono=True)
    except Exception as exc:                    # noqa: BLE001
        if _wire.is_oom(exc):
            raise _wire.oom(str(exc))
        raise _fail(_wire.CODE_REFERENCE_INVALID,
                    f"could not decode this audio ({type(exc).__name__}: {exc})")

    audio = np.nan_to_num(np.asarray(audio, dtype="float32"),
                          nan=0.0, posinf=0.0, neginf=0.0)
    seconds = audio.size / float(SAMPLE_RATE)
    if seconds < MIN_REF_SECONDS:
        raise _fail(_wire.CODE_REFERENCE_INVALID,
                    f"clip is too short ({seconds:.2f}s); use at least "
                    f"{MIN_REF_SECONDS:g} seconds of clear speech")
    if seconds > MAX_REF_SECONDS:
        audio = audio[: int(MAX_REF_SECONDS * SAMPLE_RATE)]
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak < REF_SILENCE_PEAK:
        raise _fail(_wire.CODE_REFERENCE_INVALID, "this clip is silent")

    out_raw = req.get("out")
    if not out_raw:
        # Nowhere to put it: the source is already a file the model can open.
        return {"path": str(src), "sample_rate": SAMPLE_RATE,
                "seconds": round(seconds, 3)}
    out_path = Path(str(out_raw)).expanduser()

    # Quiet references make thin clones; bring the peak up without clipping.
    written = _write_wav(audio * (0.95 / peak), out_path)
    # A freshly written reference invalidates conditioning built from the old
    # file at the same path (mtime changed anyway - this is explicit).
    STATE["ref_key"] = None
    return {"path": str(out_path), "sample_rate": SAMPLE_RATE, "seconds": written}


def _op_ping(req: dict) -> dict:
    return {
        "engine": "chatterbox",
        "loaded": STATE["model"] is not None,
        "variant": STATE["variant"],
        "device": STATE["device"],
        "sample_rate": SAMPLE_RATE,
        "pid": os.getpid(),
    }


# ── dispatch ─────────────────────────────────────────────────────────────────

def handle(op, req, send):
    if op == _wire.OP_PING:
        return _op_ping(req)
    if op == _wire.OP_LOAD:
        return _op_load(req, send)
    if op == _wire.OP_SYNTHESIZE:
        return _op_synthesize(req, send)
    if op == _wire.OP_PREPARE_REF:
        return _op_prepare_ref(req, send)
    if op == _wire.OP_TRANSCRIBE:
        # Chatterbox clones from audio alone; there is no transcript to produce
        # and none is ever required of the user.
        raise _fail(_wire.CODE_WORKER_FAILED,
                    "chatterbox needs no reference transcript")
    raise _fail(_wire.CODE_WORKER_FAILED, f"unsupported op: {op!r}")


if __name__ == "__main__":
    channel = _wire.claim_stdout()
    sys.exit(_wire.serve(handle, channel=channel))
