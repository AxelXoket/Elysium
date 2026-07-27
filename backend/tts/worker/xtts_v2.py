"""tts/worker/xtts_v2.py - Coqui XTTS-v2, the ENGINE half.

Runs in the xtts venv's own interpreter (`<xtts_env>\\Scripts\\python.exe -u
<this file>`), never in the app. It may import torch and TTS; it may import
nothing from Elysium except the sibling `_wire`, which is the protocol.

WHAT THIS FILE REFUSES TO DO
    Download. `TTS.api.TTS("tts_models/multilingual/multi-dataset/xtts_v2")` -
    the call the bake-off drivers used - goes through Coqui's ModelManager,
    which will happily reach the network, and which asks for CPML license
    consent on STDIN when it is not pre-agreed. Both are fatal here: stdin is
    the protocol channel, so one `input()` prompt would swallow a request frame
    and hang the host forever. So the model is built the low-level way instead,
    from the three local files the adapter fingerprints:

        config.json  ->  XttsConfig.load_json()
        model.pth    ->  Xtts.load_checkpoint(checkpoint_dir=...)
        vocab.json   ->  passed explicitly, so nothing is ever resolved by name

    plus speakers_xtts.pth when present, which is where the ~58 built-in
    speakers live. No name, no hub, no network - see the offline env below.

OUTPUT is 24000 Hz mono (`config.audio.output_sample_rate`), written as
16-bit PCM WAV with the stdlib `wave` module so this file does not depend on
soundfile/torchaudio being importable in that venv.

LANGUAGE CODES ARE IRREGULAR. XTTS says "zh-cn" where Chatterbox says "zh".
The value arrives from the settings UI, whose choices are read out of this
model's own config.json, so it is normally already correct - `_language()`
exists for the case where it is not, and it fails loudly rather than
synthesising Mandarin text with an English tokenizer.
"""
from __future__ import annotations

import inspect
import os
import struct
import sys
import traceback
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _wire  # noqa: E402

# Set BEFORE anything from TTS is imported.
#   COQUI_TOS_AGREED  - ModelManager asks for license consent via input() when
#                       this is unset. input() reads OUR stdin, i.e. it would
#                       eat a JSON request frame and deadlock the host.
#   *_OFFLINE         - belt and braces: even a stray HF resolve call fails
#                       fast and locally instead of touching the network.
os.environ.setdefault("COQUI_TOS_AGREED", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

ENGINE_ID = "xtts_v2"
SAMPLE_RATE = 24000                 # XTTS-v2 vocoder output, fixed
REQUIRED_FILES = ("config.json", "model.pth", "vocab.json")

# Defaults MUST match ParamSpec-for-ParamSpec with
# backend/tts/adapters/xtts_v2.py:describe_settings(). The settings UI is
# generated from that file; a name that disagrees is a knob the user turns to
# no effect. `language` is deliberately absent - its default is model-derived
# (the adapter picks "en" only when this model's config.json lists it).
#
# NOTE on repetition_penalty: the shipped config.json carries 5.0 and
# `Xtts.inference()`'s own default is 10.0 (both read, not guessed), but the
# adapter declares 2.0 and the adapter is the contract. We pass whatever comes
# in explicitly, so neither of those values can silently win.
DEFAULTS = {
    "temperature": 0.65,
    "repetition_penalty": 2.0,
    "top_k": 50,
    "top_p": 0.85,
    "length_penalty": 1.0,
    "speed": 1.0,
    "enable_text_splitting": True,
}

STATE = {
    "torch": None,          # the module, once imported
    "Xtts": None,
    "XttsConfig": None,
    "model": None,
    "config": None,
    "model_path": None,     # resolved str of the loaded model dir
    "device": None,
    "speakers": [],         # built-in speaker names from speakers_xtts.pth
    "sample_rate": SAMPLE_RATE,
    "latents": {},          # cache: ref fingerprint -> (gpt_cond_latent, emb)
}


# ── engine import ────────────────────────────────────────────────────────────

def _import_engine():
    """Import torch + TTS, or exit 3.

    Exit 3 is "the install is damaged", which the host turns into "set voice up
    again" instead of the useless "it crashed". SystemExit is a BaseException,
    so it sails past `serve()`'s handlers and becomes the process exit code -
    which is exactly what we want.
    """
    if STATE["torch"] is not None:
        return
    try:
        import torch
        from TTS.tts.configs.xtts_config import XttsConfig
        from TTS.tts.models.xtts import Xtts
    except Exception:                                   # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        sys.stderr.write("xtts_v2: engine import failed\n")
        sys.stderr.flush()
        sys.exit(_wire.EXIT_ENGINE_IMPORT)
    STATE["torch"] = torch
    STATE["XttsConfig"] = XttsConfig
    STATE["Xtts"] = Xtts


# ── small helpers ────────────────────────────────────────────────────────────

def _as_bool(value, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
    return fallback


def _as_float(value, fallback: float) -> float:
    """A finite float, or the default.

    `json.loads` accepts the bare tokens `NaN`, `Infinity` and `-Infinity`, so
    a malformed request frame really can carry them this far. Neither survives
    a sampler: NaN poisons the logits, Infinity comes back as OverflowError
    from `int()`. Both become "the knob was not set".
    """
    try:
        num = float(value)
    except (TypeError, ValueError, OverflowError):
        return fallback
    if num != num or num in (float("inf"), float("-inf")):
        return fallback
    return num


def _as_int(value, fallback: int) -> int:
    try:
        num = float(value)
    except (TypeError, ValueError, OverflowError):
        return fallback
    if num != num or num in (float("inf"), float("-inf")):
        return fallback
    try:
        return int(round(num))
    except (TypeError, ValueError, OverflowError):
        return fallback


def _named_params(fn) -> set:
    """Explicitly-named parameters of `fn` - **kwargs deliberately excluded.

    `Xtts.inference()` ends in `**hf_generate_kwargs` which is forwarded
    straight into HuggingFace `generate()`. So an unknown kwarg is NOT quietly
    ignored there; it explodes one layer deeper with a confusing message. We
    therefore drop anything that is not a real named parameter (e.g. `speed`
    on a TTS older than 0.22) rather than letting it through.
    """
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return set()
    return {
        name for name, p in sig.parameters.items()
        if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY) and name != "self"
    }


def _split_supported(fn, kwargs: dict) -> tuple[dict, list]:
    known = _named_params(fn)
    if not known:                                   # could not introspect: try all
        return dict(kwargs), []
    keep = {k: v for k, v in kwargs.items() if k in known}
    dropped = sorted(k for k in kwargs if k not in known)
    return keep, dropped


def _guard(exc: BaseException, code: str, prefix: str):
    """Turn an engine exception into the right kind of failure.

    CUDA OOM is an *expected* outcome with real advice attached, so it must
    never surface as a generic crash - `serve()` maps `_wire.oom` to exit 2.
    """
    if _wire.is_oom(exc):
        _empty_cache()
        return _wire.oom(f"{prefix}: {exc}")
    return _wire.WorkerError(code, f"{prefix}: {type(exc).__name__}: {exc}")


def _reserved_mb():
    """VRAM this process has reserved, or None on CPU. `host.py` prefers this
    over the adapter's static estimate when it is present."""
    torch = STATE["torch"]
    if torch is None:
        return None
    try:
        if not torch.cuda.is_available():
            return None
        return int(torch.cuda.memory_reserved() / (1024 * 1024))
    except Exception:                               # noqa: BLE001
        return None


def _empty_cache() -> None:
    torch = STATE["torch"]
    if torch is None:
        return
    try:
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:                               # noqa: BLE001
        pass


# ── wav out ──────────────────────────────────────────────────────────────────

def _pcm16(samples) -> bytes:
    """float32 in [-1, 1] -> little-endian int16 frames."""
    try:
        import numpy as np
    except Exception:                               # noqa: BLE001
        np = None

    if hasattr(samples, "detach"):                  # a torch tensor
        samples = samples.detach().to("cpu")
        samples = samples.numpy() if np is not None else samples.tolist()

    if np is not None:
        arr = np.asarray(samples, dtype=np.float32).reshape(-1)
        if arr.size == 0:
            return b""
        # XTTS occasionally emits a hair over 1.0; clipping is the difference
        # between a limiter and a wrap-around click.
        arr = np.clip(arr, -1.0, 1.0)
        return (arr * 32767.0).astype("<i2").tobytes()

    flat = []
    stack = [samples]
    while stack:
        item = stack.pop()
        if isinstance(item, (list, tuple)):
            stack.extend(reversed(item))
        else:
            flat.append(float(item))
    return b"".join(struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32767.0))
                    for s in flat)


def _write_wav(path: str, samples, rate: int) -> float:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)    # the host may not have
    pcm = _pcm16(samples)
    if not pcm:
        raise _wire.WorkerError(_wire.CODE_SYNTHESIS_FAILED,
                                "the engine returned no audio")
    with wave.open(str(out), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(int(rate))
        fh.writeframes(pcm)
    return round(len(pcm) / 2 / float(rate), 3)


def _wav_seconds(path: Path) -> float | None:
    """Duration of a reference clip, when it is a readable WAV. Advisory only -
    a non-wav reference is not an error, the engine's own loader handles more
    formats than we can inspect."""
    try:
        with wave.open(str(path), "rb") as fh:
            rate = fh.getframerate() or 1
            return round(fh.getnframes() / float(rate), 3)
    except Exception:                               # noqa: BLE001
        return None


# ── load ─────────────────────────────────────────────────────────────────────

def _model_dir(req: dict) -> Path:
    raw = req.get("model_path") or req.get("model_dir") or ""
    if not raw:
        raise _wire.WorkerError(_wire.CODE_WORKER_FAILED, "no model_path given")
    path = Path(str(raw))
    if not path.is_dir():
        raise _wire.WorkerError(_wire.CODE_WORKER_FAILED,
                                f"model folder does not exist: {path}")
    missing = [n for n in REQUIRED_FILES if not (path / n).is_file()]
    if missing:
        # User-fixable (re-download the folder), so it is a named failure and
        # the worker stays alive for the next attempt.
        raise _wire.WorkerError(_wire.CODE_WORKER_FAILED,
                                "model folder is missing " + ", ".join(missing))
    return path


def _pick_device(req: dict) -> str:
    torch = STATE["torch"]
    want = str(req.get("device") or (req.get("values") or {}).get("device") or "").lower()
    if want in ("cpu", "cuda"):
        if want == "cuda" and not torch.cuda.is_available():
            return "cpu"
        return want
    return "cuda" if torch.cuda.is_available() else "cpu"


def _load_checkpoint(model, config, model_dir: Path, use_deepspeed: bool) -> bool:
    """Call `Xtts.load_checkpoint` with every path pinned to a local file.
    Returns True when the checkpoint loader already put the model in eval mode.

    The `weights_only` shim: torch >= 2.6 flipped `torch.load`'s default to
    True, and XTTS's checkpoint is a full pickle, so it raises
    UnpicklingError on an otherwise perfectly good install. We are loading a
    file the user placed in their own model folder - the same trust boundary as
    running the engine at all - so we restore the old default for the duration
    of this call only, then put torch.load back exactly as we found it.
    """
    torch = STATE["torch"]
    original_load = torch.load

    def _full_load(*args, **kwargs):
        kwargs.setdefault("weights_only", False)
        return original_load(*args, **kwargs)

    speakers = model_dir / "speakers_xtts.pth"
    kwargs = {
        "checkpoint_dir": str(model_dir),
        "checkpoint_path": str(model_dir / "model.pth"),
        "vocab_path": str(model_dir / "vocab.json"),
        "eval": True,
        "use_deepspeed": bool(use_deepspeed),
    }
    if speakers.is_file():
        kwargs["speaker_file_path"] = str(speakers)
    kwargs, _dropped = _split_supported(model.load_checkpoint, kwargs)

    try:
        torch.load = _full_load
        model.load_checkpoint(config, **kwargs)
    finally:
        torch.load = original_load
    return kwargs.get("eval") is True


def _op_load(req: dict, send) -> dict:
    _import_engine()
    torch = STATE["torch"]

    model_dir = _model_dir(req)
    resolved = str(model_dir.resolve())
    device = _pick_device(req)

    # Rule 8: the same model on the same device is a no-op, not a 30-second
    # reload of 2.6 GB of weights.
    if STATE["model"] is not None and STATE["model_path"] == resolved \
            and STATE["device"] == device:
        return {
            "loaded": True, "cached": True, "engine": ENGINE_ID,
            "device": device, "sample_rate": STATE["sample_rate"],
            "speakers": list(STATE["speakers"]),
            "languages": list(getattr(STATE["config"], "languages", []) or []),
            "vram_mb": _reserved_mb(),
            "cuda": bool(getattr(torch, "cuda", None) and torch.cuda.is_available()),
        }

    _unload()
    send(_wire.event("progress", stage="reading config", pct=0.05))

    config = STATE["XttsConfig"]()
    try:
        config.load_json(str(model_dir / "config.json"))
    except Exception as exc:                        # noqa: BLE001
        raise _wire.WorkerError(_wire.CODE_WORKER_FAILED,
                                f"config.json is unreadable: {exc}")

    send(_wire.event("progress", stage="building model", pct=0.15))
    try:
        model = STATE["Xtts"].init_from_config(config)
        send(_wire.event("progress", stage="loading weights", pct=0.35))
        already_eval = _load_checkpoint(
            model, config, model_dir, _as_bool(req.get("use_deepspeed"), False))
        send(_wire.event("progress", stage="moving to device", pct=0.85))
        model.to(device)
        # `Xtts.eval()` is NOT the plain nn.Module one: it re-runs
        # `gpt.init_gpt_for_inference()` with that method's own defaults, which
        # builds a SECOND inference GPT and - the actual bug - throws away the
        # DeepSpeed engine `load_checkpoint(use_deepspeed=True)` just built,
        # leaving its fp16 copy stranded on the card. load_checkpoint(eval=True)
        # has already done all of the engine-specific eval work (hifigan.eval(),
        # init_gpt_for_inference(kv_cache=args.kv_cache, use_deepspeed=...),
        # gpt.eval()), so the only thing left to do is flip the module flag.
        if already_eval:
            torch.nn.Module.eval(model)
        else:
            model.eval()
    except Exception as exc:                        # noqa: BLE001
        if _wire.is_oom(exc):
            _unload()
            raise _wire.oom(f"loading XTTS-v2: {exc}")
        # The environment is fine, THIS model would not load - and a
        # half-initialised model sitting on the card is not worth keeping, so
        # we go away with the code that says exactly that (host: exit 4 ->
        # tts_worker_failed, with our stderr tail as the reason).
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        _unload()
        sys.exit(_wire.EXIT_MODEL_LOAD)

    speaker_manager = getattr(model, "speaker_manager", None)
    names = getattr(speaker_manager, "speakers", None) or {}
    audio = getattr(config, "audio", None)

    STATE["model"] = model
    STATE["config"] = config
    STATE["model_path"] = resolved
    STATE["device"] = device
    STATE["speakers"] = sorted(str(n) for n in names)
    STATE["sample_rate"] = int(getattr(audio, "output_sample_rate", 0) or SAMPLE_RATE)
    STATE["latents"] = {}

    send(_wire.event("progress", stage="ready", pct=1.0))
    return {
        "loaded": True, "cached": False, "engine": ENGINE_ID,
        "device": device, "sample_rate": STATE["sample_rate"],
        "speakers": list(STATE["speakers"]),
        "languages": list(getattr(config, "languages", []) or []),
        # host.py reads this and only falls back to the static 2600 MB estimate
        # when it is missing, so reporting the truth keeps the VRAM ledger honest.
        "vram_mb": _reserved_mb(),
        "cuda": bool(getattr(torch, "cuda", None) and torch.cuda.is_available()),
    }


def _unload() -> None:
    STATE["model"] = None
    STATE["config"] = None
    STATE["model_path"] = None
    STATE["device"] = None
    STATE["speakers"] = []
    STATE["latents"] = {}
    _empty_cache()


def _require_model():
    if STATE["model"] is None:
        raise _wire.WorkerError(_wire.CODE_WORKER_FAILED, "no model is loaded")
    return STATE["model"]


# ── language ─────────────────────────────────────────────────────────────────

def _language(values: dict, req: dict) -> str:
    """Resolve the language code XTTS actually wants.

    XTTS is the odd one out: "zh-cn", not "zh". The choices the UI offers come
    from this model's own config.json, so a mismatch here means a stale saved
    value or a caller that bypassed the adapter - either way, guessing would
    produce fluent-sounding nonsense, so we name the problem instead.
    """
    supported = [str(x) for x in (getattr(STATE["config"], "languages", None) or [])]
    raw = values.get("language") or req.get("language") or ""
    code = str(raw).strip().lower().replace("_", "-")
    if code in ("zh", "zh-chs", "cmn"):
        code = "zh-cn"                              # the irregular one
    if not code:
        code = "en" if (not supported or "en" in supported) else supported[0]
    if supported and code not in supported:
        raise _wire.WorkerError(
            _wire.CODE_SYNTHESIS_FAILED,
            f"language '{code}' is not one of this model's "
            f"{len(supported)} languages ({', '.join(supported[:8])}...)")
    return code


# ── reference voice / conditioning ───────────────────────────────────────────

def _reference_path(values: dict, req: dict) -> Path | None:
    """The reference clip, if the caller supplied one.

    `reference_voice` is the ParamSpec name (type voice_ref) in the adapter;
    the top-level request keys are accepted too because the host resolves a
    voice id to a real file before it gets here.
    """
    raw = (req.get("speaker_wav") or req.get("reference_wav")
           or req.get("reference") or values.get("reference_voice") or "")
    raw = str(raw).strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_file():
        raise _wire.WorkerError(_wire.CODE_REFERENCE_INVALID,
                                f"reference clip not found: {path.name}")
    return path


def _ref_key(path: Path) -> tuple:
    try:
        st = path.stat()
        return (str(path.resolve()), int(st.st_mtime), int(st.st_size))
    except OSError:
        return (str(path), 0, 0)


def _conditioning_from_wav(path: Path):
    """Clone from audio alone - XTTS needs NO transcript for this.

    The bake-off drivers never exercised cloning on this engine (both used the
    built-in preset speakers), so the kwargs here were taken from
    `Xtts.get_conditioning_latents()`'s real signature - the same call
    `synthesize()` makes internally. It HAS since been run end to end against
    tts_bakeoff/refs/ref1.wav on this machine: latents computed, cached, saved
    and spoken.
    """
    model = _require_model()
    config = STATE["config"]
    key = _ref_key(path)
    cached = STATE["latents"].get(key)
    if cached is not None:
        return cached

    kwargs = {
        "audio_path": str(path),
        "gpt_cond_len": getattr(config, "gpt_cond_len", 30),
        "gpt_cond_chunk_len": getattr(config, "gpt_cond_chunk_len", 6),
        "max_ref_length": getattr(config, "max_ref_len", 30),
        "sound_norm_refs": getattr(config, "sound_norm_refs", False),
    }
    kwargs, _dropped = _split_supported(model.get_conditioning_latents, kwargs)
    try:
        gpt_cond_latent, speaker_embedding = model.get_conditioning_latents(**kwargs)
    except Exception as exc:                        # noqa: BLE001
        # A clip that is too short, silent, or not really audio lands here, and
        # every one of those is fixable by the person who chose the file.
        raise _guard(exc, _wire.CODE_REFERENCE_INVALID,
                     f"could not use reference '{path.name}'")

    STATE["latents"][key] = (gpt_cond_latent, speaker_embedding)
    if len(STATE["latents"]) > 8:                   # tiny tensors, but not free
        STATE["latents"].pop(next(iter(STATE["latents"])))
    return gpt_cond_latent, speaker_embedding


def _conditioning_from_speaker(name: str):
    model = _require_model()
    manager = getattr(model, "speaker_manager", None)
    speakers = getattr(manager, "speakers", None) or {}
    entry = speakers.get(name)
    if entry is None:
        raise _wire.WorkerError(
            _wire.CODE_REFERENCE_INVALID,
            f"unknown built-in speaker '{name}'")
    # Verified against the real speakers_xtts.pth: 58 entries, each a dict with
    # exactly these two keys ([1,32,1024] and [1,512,1]).
    gpt_cond_latent = entry["gpt_cond_latent"]
    speaker_embedding = entry["speaker_embedding"]
    device = STATE["device"] or "cpu"
    to = lambda t: t.to(device) if hasattr(t, "to") else t    # noqa: E731
    try:
        return to(gpt_cond_latent), to(speaker_embedding)
    except Exception as exc:                        # noqa: BLE001
        # Small tensors, but a card that is already full says no to these too,
        # and that has to come out as an OOM with advice, not a generic crash.
        raise _guard(exc, _wire.CODE_SYNTHESIS_FAILED,
                     f"could not use speaker '{name}'")


def _conditioning(values: dict, req: dict) -> tuple:
    """(gpt_cond_latent, speaker_embedding, how) for this request."""
    ref = _reference_path(values, req)
    if ref is not None:
        latent, emb = _conditioning_from_wav(ref)
        return latent, emb, f"clone:{ref.name}"

    wanted = str(req.get("speaker") or values.get("speaker") or "").strip()
    if wanted:
        latent, emb = _conditioning_from_speaker(wanted)
        return latent, emb, f"speaker:{wanted}"

    if STATE["speakers"]:
        # No reference and no choice: the first built-in keeps voice working
        # instead of failing, and the result says which one spoke.
        name = STATE["speakers"][0]
        latent, emb = _conditioning_from_speaker(name)
        return latent, emb, f"speaker:{name}"

    raise _wire.WorkerError(
        _wire.CODE_REFERENCE_INVALID,
        "no reference voice given and this model has no built-in speakers "
        "(speakers_xtts.pth is missing)")


# ── synthesize ───────────────────────────────────────────────────────────────

def _op_synthesize(req: dict, send) -> dict:
    model = _require_model()
    values = req.get("values") or {}

    text = str(req.get("text") or "").strip()
    if not text:
        raise _wire.WorkerError(_wire.CODE_SYNTHESIS_FAILED, "no text to speak")
    out = str(req.get("out") or "").strip()
    if not out:
        raise _wire.WorkerError(_wire.CODE_SYNTHESIS_FAILED, "no output path given")

    language = _language(values, req)
    send(_wire.event("progress", stage="conditioning", pct=0.1))
    gpt_cond_latent, speaker_embedding, how = _conditioning(values, req)

    # Names and defaults straight out of adapters/xtts_v2.py. Nothing outside
    # DEFAULTS is ever forwarded: the settings payload is not a kwargs pipe.
    kwargs = {
        "temperature": _as_float(values.get("temperature"), DEFAULTS["temperature"]),
        "repetition_penalty": _as_float(values.get("repetition_penalty"),
                                        DEFAULTS["repetition_penalty"]),
        "top_k": _as_int(values.get("top_k"), DEFAULTS["top_k"]),
        "top_p": _as_float(values.get("top_p"), DEFAULTS["top_p"]),
        "length_penalty": _as_float(values.get("length_penalty"),
                                    DEFAULTS["length_penalty"]),
        "speed": _as_float(values.get("speed"), DEFAULTS["speed"]),
        "enable_text_splitting": _as_bool(values.get("enable_text_splitting"),
                                          DEFAULTS["enable_text_splitting"]),
    }
    # A library version that dropped a kwarg degrades to "that knob did
    # nothing" instead of crashing - the bake-off drivers' own convention.
    kwargs, ignored = _split_supported(model.inference, kwargs)

    send(_wire.event("progress", stage="synthesizing", pct=0.35))
    try:
        result = model.inference(text, language, gpt_cond_latent,
                                 speaker_embedding, **kwargs)
    except TypeError as exc:
        # Last-resort retry with the minimal call, so a signature change is a
        # quality regression rather than an outage.
        sys.stderr.write(f"xtts_v2: inference kwargs rejected ({exc}); "
                         f"retrying minimal\n")
        try:
            result = model.inference(text, language, gpt_cond_latent,
                                     speaker_embedding)
            ignored = sorted(set(ignored) | set(kwargs))
        except Exception as inner:                  # noqa: BLE001
            raise _guard(inner, _wire.CODE_SYNTHESIS_FAILED, "synthesis failed")
    except Exception as exc:                        # noqa: BLE001
        raise _guard(exc, _wire.CODE_SYNTHESIS_FAILED, "synthesis failed")

    wav = result.get("wav") if isinstance(result, dict) else result
    if wav is None:
        raise _wire.WorkerError(_wire.CODE_SYNTHESIS_FAILED,
                                "the engine returned no waveform")

    rate = int(STATE["sample_rate"] or SAMPLE_RATE)
    send(_wire.event("progress", stage="writing", pct=0.92))
    seconds = _write_wav(out, wav, rate)

    return {
        "path": out,
        "sample_rate": rate,
        "seconds": seconds,
        "engine": ENGINE_ID,
        "language": language,
        "voice": how,
        "ignored_params": ignored,
    }


# ── prepare_ref ──────────────────────────────────────────────────────────────

def _op_prepare_ref(req: dict, send) -> dict:
    """Pre-compute (and cache) the conditioning latents for a reference clip.

    XTTS needs no transcript, so this op is purely a warm-up: doing it here
    means the first sentence the user hears is not paying for it. Optionally
    persists the latents to `req["out"]` (the voices/<id>/latents.pth slot in
    the layout) - torch.save only, never audio.
    """
    _require_model()
    values = req.get("values") or {}
    ref = _reference_path(values, req)
    if ref is None:
        raise _wire.WorkerError(_wire.CODE_REFERENCE_INVALID,
                                "no reference clip given")

    send(_wire.event("progress", stage="encoding reference", pct=0.3))
    gpt_cond_latent, speaker_embedding = _conditioning_from_wav(ref)

    saved = None
    out = str(req.get("out") or "").strip()
    if out:
        try:
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            STATE["torch"].save(
                {"gpt_cond_latent": gpt_cond_latent.detach().to("cpu"),
                 "speaker_embedding": speaker_embedding.detach().to("cpu")},
                out)
            saved = out
        except Exception as exc:                    # noqa: BLE001
            # The in-memory cache is the real product here; failing to write
            # the sidecar must not cost the user their voice.
            sys.stderr.write(f"xtts_v2: could not cache latents: {exc}\n")

    return {
        "ok": True,
        "reference": str(ref),
        "seconds": _wav_seconds(ref),
        "needs_transcript": False,      # audio alone is enough for XTTS
        "cached": True,
        "latents_path": saved,
        "engine": ENGINE_ID,
    }


# ── dispatch ─────────────────────────────────────────────────────────────────

def handle(op, req, send):
    if op == _wire.OP_PING:
        # Deliberately cheap: a ping must not be the thing that drags 2 GB of
        # torch into a process that has not been asked to load anything yet.
        torch = STATE["torch"]
        return {
            "pong": True,
            "pid": os.getpid(),
            "engine": ENGINE_ID,
            "loaded": STATE["model"] is not None,
            "model_path": STATE["model_path"],
            "device": STATE["device"],
            "sample_rate": STATE["sample_rate"],
            "torch": getattr(torch, "__version__", None) if torch else None,
        }

    if op == _wire.OP_LOAD:
        return _op_load(req, send)

    if op == _wire.OP_SYNTHESIZE:
        return _op_synthesize(req, send)

    if op == _wire.OP_PREPARE_REF:
        return _op_prepare_ref(req, send)

    if op == _wire.OP_TRANSCRIBE:
        # Saying "unsupported" beats a stub that returns "": XTTS clones from
        # audio alone, so nothing upstream should ever need this.
        raise _wire.WorkerError(
            _wire.CODE_WORKER_FAILED,
            "XTTS-v2 cannot transcribe - and needs no transcript to clone")

    raise _wire.WorkerError(_wire.CODE_WORKER_FAILED, f"unknown op {op}")


if __name__ == "__main__":
    channel = _wire.claim_stdout()      # stdout belongs to the protocol now
    sys.exit(_wire.serve(handle, channel=channel))
