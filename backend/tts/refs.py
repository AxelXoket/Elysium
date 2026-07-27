"""tts/refs.py - the voices a model clones from.

A reference voice is a short clip of somebody speaking, and for some engines
the words that were said. Both belong to the user, so:

  * they live as plain files in `<data>/voice/refs/<voice_id>/`, not in the
    encrypted database. They are files the user chose and put there; making
    them only reachable through Elysium would take away their own recordings.
  * nothing about them is uploaded. Transcription is done by the engine's own
    runtime on this machine.

WHY A TRANSCRIPT AT ALL
    Fish cannot clone from audio alone - it conditions on the audio AND the
    text of that audio, and with one missing it falls back to a generic voice
    without saying so. That silent fallback is worse than an error, so a
    missing transcript is refused loudly (tts_transcript_required) with the
    offer to listen and fill it in.

    The filled-in text must stay EDITABLE: Whisper mishears, and in the
    bake-off it turned "you're mine" into "your mind". A wrong transcript does
    not fail - it quietly degrades the cloned voice, which is the hardest kind
    of problem for someone to diagnose.

HOST HALF
    Only stdlib here. WAV is inspected with `wave`; anything else is accepted
    and marked as needing conversion, which the engine runtime does when the
    voice is first used. Reaching for an audio library in the app process
    would put a decoder in the path of every launch for no benefit.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import config

from .errors import (
    TTS_REFERENCE_INVALID,
    TTS_REFERENCE_TOO_SHORT,
    TTS_TRANSCRIPT_REQUIRED,
    TtsError,
)

logger = logging.getLogger(__name__)

VOICE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
AUDIO_SUFFIXES = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".opus"}
META_NAME = "voice.json"
TRANSCRIPT_NAME = "transcript.txt"


class RefError(TtsError):
    pass


@dataclass(frozen=True)
class ReferenceVoice:
    voice_id: str
    label: str
    path: str
    audio_name: str
    transcript: str
    transcript_source: str          # "user" | "auto" | "none"
    seconds: float | None
    needs_conversion: bool

    @property
    def has_transcript(self) -> bool:
        return bool(self.transcript.strip())

    def to_json(self) -> dict:
        return {
            "voice_id": self.voice_id,
            "label": self.label,
            "path": self.path,
            "audio_name": self.audio_name,
            "transcript": self.transcript,
            "transcript_source": self.transcript_source,
            "seconds": self.seconds,
            "needs_conversion": self.needs_conversion,
            "has_transcript": self.has_transcript,
        }


def refs_dir() -> Path:
    return Path(config.TTS_REFS_DIR)


def _voice_dir(voice_id: str) -> Path:
    if not VOICE_ID.match(voice_id or ""):
        # The id becomes a directory name. Anything that is not a plain slug
        # is refused rather than sanitised, so there is no path to smuggle.
        raise RefError(TTS_REFERENCE_INVALID, "not a usable voice id")
    return refs_dir() / voice_id


def _read_meta(folder: Path) -> dict:
    try:
        with open(folder / META_NAME, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:                           # noqa: BLE001
        return {}


def _write_meta(folder: Path, data: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / META_NAME, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def _audio_in(folder: Path) -> Path | None:
    for child in sorted(folder.iterdir()):
        if child.is_file() and child.suffix.lower() in AUDIO_SUFFIXES:
            return child
    return None


def wav_seconds(path: Path) -> float | None:
    """Length of a WAV, or None if it is not one we can read. Never raises:
    an unreadable header is a validation result, not a crash."""
    try:
        with wave.open(str(path), "rb") as wf:
            rate = wf.getframerate()
            if not rate:
                return None
            return wf.getnframes() / float(rate)
    except Exception:                           # noqa: BLE001
        return None


def describe(voice_id: str) -> ReferenceVoice:
    folder = _voice_dir(voice_id)
    if not folder.is_dir():
        raise RefError(TTS_REFERENCE_INVALID, "no such voice")
    audio = _audio_in(folder)
    if audio is None:
        raise RefError(TTS_REFERENCE_INVALID, "the voice folder has no audio in it")
    meta = _read_meta(folder)
    transcript = ""
    tpath = folder / TRANSCRIPT_NAME
    if tpath.is_file():
        try:
            transcript = tpath.read_text(encoding="utf-8").strip()
        except OSError:
            transcript = ""
    seconds = wav_seconds(audio) if audio.suffix.lower() == ".wav" else None
    return ReferenceVoice(
        voice_id=voice_id,
        label=str(meta.get("label") or voice_id),
        path=str(folder),
        audio_name=audio.name,
        transcript=transcript,
        transcript_source=str(meta.get("transcript_source")
                              or ("user" if transcript else "none")),
        seconds=seconds,
        needs_conversion=audio.suffix.lower() != ".wav",
    )


def list_voices() -> list[ReferenceVoice]:
    root = refs_dir()
    if not root.is_dir():
        return []
    out = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        try:
            out.append(describe(child.name))
        except RefError:
            # A folder the user is still filling in should not break the list.
            logger.debug("tts: skipping unusable voice folder %s", child.name)
    return out


def validate(voice: ReferenceVoice) -> None:
    """Refuse what cannot work, with the reason that leads to a fix."""
    if voice.seconds is not None:
        if voice.seconds < float(config.TTS_REF_MIN_S):
            raise RefError(
                TTS_REFERENCE_TOO_SHORT,
                "%.1f seconds; around ten works best" % voice.seconds,
            )
        if voice.seconds > float(config.TTS_REF_MAX_S) * 4:
            # Long clips are not wrong, only wasteful - but an hour-long file
            # is a mistake worth catching before it is encoded.
            raise RefError(TTS_REFERENCE_INVALID,
                           "that clip is far longer than a voice sample needs to be")


def save_upload(voice_id: str, filename: str, data: bytes, *,
                label: str = "", transcript: str = "") -> ReferenceVoice:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in AUDIO_SUFFIXES:
        raise RefError(TTS_REFERENCE_INVALID, "that is not an audio file")
    if not data:
        raise RefError(TTS_REFERENCE_INVALID, "the file is empty")
    if len(data) > int(config.TTS_REF_MAX_BYTES):
        raise RefError(TTS_REFERENCE_INVALID, "that file is too large for a voice sample")

    folder = _voice_dir(voice_id)
    folder.mkdir(parents=True, exist_ok=True)

    # VALIDATE BEFORE DESTROYING. This used to unlink the existing clip, write
    # the new bytes, and only then call validate() - so a clip refused as too
    # short had ALREADY replaced a good take, the API answered 400, and the
    # user's only copy of the working recording was gone. Nothing re-validates
    # at speak time either (_expand_reference calls describe, not validate), so
    # the next reply cloned from the file the API had just rejected.
    #
    # Staging lives in the refs ROOT, not in the voice folder: _audio_in picks
    # the first audio file in sorted order, so a leftover staged file inside
    # the folder could become the voice.
    staged = refs_dir() / (".incoming-" + voice_id + suffix)
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(data)
    try:
        candidate = ReferenceVoice(
            voice_id=voice_id,
            label=label or voice_id,
            path=str(folder),
            audio_name="ref" + suffix,
            transcript=transcript.strip(),
            transcript_source="user" if transcript.strip() else "none",
            seconds=wav_seconds(staged) if suffix == ".wav" else None,
            needs_conversion=suffix != ".wav",
        )
        validate(candidate)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise

    for old in list(folder.iterdir()):
        if old.is_file() and old.suffix.lower() in AUDIO_SUFFIXES:
            old.unlink()                       # one clip per voice
    staged.replace(folder / ("ref" + suffix))

    # The transcript belongs to the RECORDING, not to the voice id. Writing it
    # only when supplied left a re-upload pairing take 2's audio with take 1's
    # words: describe() reported has_transcript=True, require_transcript passed,
    # and Fish was conditioned on the wrong text - a degraded clone reported as
    # success. The payload contradicted itself too (transcript_source "none"
    # alongside a non-empty transcript), so the UI could not even flag it.
    tpath = folder / TRANSCRIPT_NAME
    if transcript.strip():
        tpath.write_text(transcript.strip(), encoding="utf-8")
    else:
        tpath.unlink(missing_ok=True)

    _write_meta(folder, {
        "label": label or voice_id,
        "added_at": time.time(),
        "transcript_source": "user" if transcript.strip() else "none",
    })
    return describe(voice_id)


def set_transcript(voice_id: str, text: str, source: str = "user") -> ReferenceVoice:
    """Write the words. Always allowed to overwrite - an auto transcript is a
    first draft, and correcting it is the expected next step."""
    folder = _voice_dir(voice_id)
    if not folder.is_dir():
        raise RefError(TTS_REFERENCE_INVALID, "no such voice")
    (folder / TRANSCRIPT_NAME).write_text((text or "").strip(), encoding="utf-8")
    meta = _read_meta(folder)
    meta["transcript_source"] = source if (text or "").strip() else "none"
    _write_meta(folder, meta)
    return describe(voice_id)


def delete(voice_id: str) -> bool:
    """Remove a reference voice. Returns whether it is actually gone.

    `ignore_errors=True` is right - a clip the worker still has open under
    sf.read, or one Defender is mid-scan on, must not turn a delete into a
    500 - but it swallows the outcome, and the caller answered a hardcoded
    removed: True on top of it. The folder then reappeared in the list on the
    next refetch, with no way to tell that the delete had not happened.

    Deleting something that was never there is still success: the caller
    asked for it to be gone, and it is.
    """
    folder = _voice_dir(voice_id)
    if not folder.is_dir():
        return True
    shutil.rmtree(folder, ignore_errors=True)
    return not folder.exists()


def require_transcript(voice: ReferenceVoice, engine_needs_transcript: bool) -> None:
    """The check that stops a silent fallback.

    Fish conditions on audio AND text; with the text missing it produces a
    generic voice and says nothing. Refusing here is what turns an inexplicably
    wrong voice into a sentence the user can act on.
    """
    if engine_needs_transcript and not voice.has_transcript:
        raise RefError(
            TTS_TRANSCRIPT_REQUIRED,
            "this engine needs the words that are spoken in the clip",
        )
