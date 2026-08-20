"""tts/refs.py - the voices a model clones from.

A reference voice is a short clip of somebody speaking, and for some engines
the words that were said. Both belong to the user, so:

  * they live as plain files in `<data>/voice/refs/<opaque folder>/`, not in
    the encrypted database. They are files the user chose and put there;
    making them only reachable through Elysium would take away their own
    recordings.
  * nothing about them is uploaded. Transcription is done by the engine's own
    runtime on this machine.

THE FOLDER NAME IS NOT THE ID
    `voice_id` is chosen by the person, on screen (the frontend slugs the
    label they typed), and it is also the API's own identifier for the voice
    - both are fine, both are how a numeric-or-slug id is meant to be used.
    What is not fine is spelling that id onto the filesystem: a folder named
    after the label turns `dir voice\\refs` into a readable roster of who the
    user has cloned, with no passphrase and nothing to unlock, surviving
    every lock the vault ever does. The owner's rule draws the line at what a
    person can READ: a name on screen must never sit outside the vault as a
    name on disk.

    So the directory a voice actually lives in is `sha256(key + voice_id)`,
    `key` a random 32 bytes generated once per refs folder and read back on
    every later run (see `_index_key`). One-way, not a lookup table: nobody
    with just the folder names, or with the id and a guess at the scheme
    but not the key, can rebuild the label. `voice.json` INSIDE the folder
    still names the voice explicitly - that is unchanged, and it is the
    already-disclosed half of this (the app's own vault-creation screen says
    a cloning reference "stays on disk with its transcript"). Reading a
    folder's own contents was always the accepted trade; reading the ROSTER
    from names alone was not, and is what this closes.

    `list_voices()` recovers which id lives where by reading `voice.json`,
    not the folder name - the folder name carries nothing to recover FROM.

    Existing installs migrate on first touch, in-process, resumable: see
    `migrate_legacy_voice_dirs`.

WHY THE TRANSCRIPT STAYS A PLAIN FILE, NOT VAULT ROWS
    `transcript.txt` is text the user typed, and by the letter of the rule
    that is content, which argues for moving it behind the passphrase like
    everything else the user writes. It does not move, for a reason specific
    to this module rather than a general exemption:

    routers/tts_runtime.py's voice endpoints (list/upload/set transcript/
    transcribe/delete) read no vault setting and sit behind no vault
    dependency - confirmed by reading the router, not assumed. That is
    deliberate elsewhere in the same file: `_values_for` explicitly lets a
    locked-vault failure (`VaultLockedError`) propagate rather than degrading
    to empty settings, because running the engine on silent defaults while
    pretending nothing happened is worse than the 423. Reference-voice
    management was built to the OPPOSITE contract on purpose - the shipped
    frontend only ever reaches it after unlock (`VaultGate`), but the backend
    promise is not "the frontend happens to gate this", it is that
    `tts/refs.py` keeps working whether or not a key is sitting in
    `vault_state`. Putting the transcript in the encrypted DB would make
    every describe/save/edit/delete call a DB call, and the honest behaviour
    for a locked vault is then a 423 - for a file that today survives a lock
    on purpose, the same way `ref.wav` does.

    The vault-creation screen already tells the user this, in words: "any
    voice clip you add for cloning ... stays on disk with its transcript."
    Moving the transcript in would make that sentence false without a
    frontend change, which is out of this file's ownership.

    What DOES change here: the transcript's association with a LABEL is no
    longer visible from outside the folder, because the folder name that
    used to spell the label out now does not. Opening the file was always
    disclosed; finding it by browsing was not, and is closed the same way
    the audio is.

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

import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import config
import secure_delete

from .errors import (
    TTS_REFERENCE_CLIP_STUCK,
    TTS_REFERENCE_FOLDER_REDIRECTED,
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
#: The per-refs-folder salt that makes a directory name unrecoverable without
#: it. A dot-prefixed name is enough to keep it out of anyone's way; it is
#: not itself a secret the app relies on to be hidden, only to be RANDOM -
#: `list_voices()` already skips it (it is a file, not a directory).
INDEX_KEY_NAME = ".voice-index-key"


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


#: Serialises _index_key WITHIN this process. O_EXCL settles the race between
#: PROCESSES - two of them cannot both create the file - but it settles nothing
#: for threads that arrive at the empty-file repair together: each mints its
#: own key, each replaces the file, and each re-reads at a different instant,
#: so three threads walked away with three different salts and therefore three
#: different folder names for the same voice. Measured with four threads
#: before this lock existed. The lock is uncontended in the normal path (the
#: file is there and is read), so the cost is a few nanoseconds on a call that
#: already touches the disk.
_index_key_lock = threading.Lock()

#: Set once the odd-size warning below has been emitted, so it is not repeated
#: on every _hash_name call for the lifetime of the process.
_odd_key_size_reported = False


def _index_key() -> bytes:
    """32 random bytes, generated once per refs folder, read back forever
    after. This is what makes `_hash_name` more than a bare hash of the id:
    without it, someone who already suspects a label (`mom`, a first name,
    a character's name) could just hash the same guesses and match folder
    names with no access to anything but `dir`. With it, that dictionary
    attack needs this file too - not a strong secret (anyone with it can
    already open `voice.json` directly), but it closes the cheapest version
    of the same leak the folder rename exists to close.

    O_CREAT | O_EXCL makes the create race-safe ACROSS PROCESSES: a second
    process that loses the race to create it simply reads what the winner
    wrote, rather than each computing its own key and disagreeing forever
    after. Threads inside one process are held apart by `_index_key_lock`
    instead - see the note on that lock for what happened without it.
    """
    with _index_key_lock:
        return _index_key_locked()


def _index_key_locked() -> bytes:
    global _odd_key_size_reported
    path = refs_dir() / INDEX_KEY_NAME
    try:
        data = path.read_bytes()
        if len(data) == 32:
            return data
    except OSError:
        pass
    refs_dir().mkdir(parents=True, exist_ok=True)
    key = os.urandom(32)
    # O_BINARY matters here and is not decoration. Without it, os.open on
    # Windows defaults to TEXT mode, and 32 random bytes have roughly a 1 in
    # 9 chance of containing a 0x0A byte - which text mode then writes to
    # disk as the two bytes 0x0D 0x0A. The IN-MEMORY `key` below is still the
    # clean 32 bytes, but every LATER call reads the corrupted, longer file
    # back - so the very next
    # `_hash_name` call in the same request hashed a different key than this
    # one did, and `save_upload` computed one folder to create and a second,
    # different one to look for. Caught by this module's own migration test
    # suite failing intermittently, not by inspection - os.O_BINARY does not
    # exist on POSIX, hence getattr with a zero fallback.
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(str(path), flags, 0o600)
    except FileExistsError:
        # Lost the race - somebody else's key is now the file's content, or
        # will be in a moment.
        #
        # This branch used to `return path.read_bytes()` unchecked, and that
        # was a silent, permanent loss of the salt. Two ways to get a file
        # here that is not 32 bytes: the winner of the race has created it but
        # has not written to it YET (a real, sub-millisecond window between
        # the os.open and the os.write below), or a crash/power loss landed in
        # exactly that window and left a 0-byte file forever. In the second
        # case every later call returned b"" and `_hash_name` degraded to a
        # bare sha256 of the voice id - which is precisely the dictionary
        # attack this file exists to prevent, running with no sign that
        # anything was wrong and no way to notice or recover.
        #
        # So: give the winner a moment, then repair - but ONLY an empty file.
        #
        # Empty is the one length that is both broken and safe to replace.
        # Broken, because b"" is not a salt. Safe, because a key that was
        # never returned can never have named a folder, so there is nothing
        # to orphan by choosing a different one.
        #
        # ANY other length is left exactly as it is, and that is deliberate.
        # A first draft of this raised on "wrong size", and it would have
        # bricked the installs it was meant to protect: the one corruption
        # this module has actually SEEN makes the file LONGER, not shorter -
        # pre-O_BINARY text mode rewrote each 0x0A in the key as 0x0D 0x0A,
        # so those installs carry 33 to 36 bytes. The old code returned them
        # unchanged, `_hash_name` was self-consistent with them, and every
        # voice folder resolved. The bytes are still 32 bytes of entropy in a
        # non-canonical encoding; refusing them would take a working install
        # and cut it off from its own voices to fix nothing. Same argument for
        # a hypothetical short-but-not-empty file: whatever wrote it, the
        # install has been hashing with it and is internally consistent.
        for _ in range(50):                     # ~0.5s, generous for a write
            try:
                data = path.read_bytes()
            except OSError:
                data = b""
            if data:
                if len(data) != 32 and not _odd_key_size_reported:
                    # ONCE per process, not once per call. _hash_name calls
                    # this for every voice, so list_voices() on a CRLF-expanded
                    # install logged two warnings per voice per refresh -
                    # measured at six lines for three voices, and elysium.log
                    # grew on every listing. It names nothing either way (a
                    # file length is not content and not an on-screen name),
                    # but a fact that does not change does not need repeating.
                    _odd_key_size_reported = True
                    logger.warning(
                        "tts: the voice index key is %d bytes, not 32; "
                        "using it as-is", len(data))
                return data
            time.sleep(0.01)
        # Still empty after the wait: rebuild it. Atomic, so a reader that
        # arrives mid-repair sees either the old empty file or a whole key,
        # never a half-written one.
        #
        # The temp name carries pid and thread id because the wait loop above
        # SYNCHRONISES every waiter: they all give up at the same moment and
        # all arrive here together. With one shared `.new` name that is four
        # threads racing on one file, and on Windows os.replace answers that
        # with an unhandled WinError 32 - a 500, not a handled TTS error.
        # Measured, four threads, three died. Losing the race is also not an
        # error: the winner's key is just as good, so re-read rather than
        # return the local `key` that never reached the disk.
        tmp = path.with_name(
            f"{path.name}.new-{os.getpid()}-{threading.get_ident()}")
        try:
            fd2 = os.open(str(tmp), os.O_CREAT | os.O_TRUNC | os.O_WRONLY
                          | getattr(os, "O_BINARY", 0), 0o600)
            try:
                os.write(fd2, key)
                os.fsync(fd2)
            finally:
                os.close(fd2)
            os.replace(str(tmp), str(path))
            logger.warning("tts: the voice index key was empty and was rebuilt")
        except OSError:
            # Somebody else got there first, or the disk said no. Either way
            # the file on disk is the authority, not this thread's `key`.
            try:
                tmp.unlink()
            except OSError:
                pass
        # Re-read with patience, and not only because a loser needs the
        # winner's bytes. On Windows the read ITSELF fails with
        # PermissionError while somebody else's os.replace is in flight over
        # the same name - measured here with four threads, where two died on
        # a read that sat outside this loop. A rename is not instantaneous
        # from a reader's point of view, so treat a refused read as "not yet"
        # rather than as an answer.
        final = b""
        for _ in range(50):
            try:
                final = path.read_bytes()
            except OSError:
                final = b""
            if final:
                break
            time.sleep(0.01)
        if len(final) != 32:
            # Nothing wrote a usable key and this thread's attempt did not
            # land either. Refusing is right here: returning b"" would put the
            # unsalted hash back, which is the whole defect.
            raise RefError(
                TTS_REFERENCE_INVALID, "the voice index key could not be built")
        return final
    try:
        os.write(fd, key)
        # The whole point of this file is that it survives; an fsync here is
        # what makes the crash window above rare rather than merely narrow.
        os.fsync(fd)
    finally:
        os.close(fd)
    return key


def _hash_name(voice_id: str) -> str:
    """The opaque folder name for a voice id. Deterministic and one-way: the
    same id always lands on the same folder (so restarts resolve it with no
    index file to consult), and the folder name alone gives nothing back."""
    return hashlib.sha256(_index_key() + voice_id.encode("utf-8")).hexdigest()


_migrate_lock = threading.Lock()
#: Which refs roots have already been checked for legacy folders THIS
#: PROCESS. Keyed by the resolved root path rather than a single bool so
#: tests that repoint `config.TTS_REFS_DIR` per-case do not see a migration
#: that ran once for an earlier root silently skip a later, different one.
_migrated_roots: set[str] = set()


def _ensure_migrated() -> None:
    root = str(refs_dir())
    if root in _migrated_roots:
        return
    with _migrate_lock:
        if root in _migrated_roots:
            return
        migrate_legacy_voice_dirs()
        _migrated_roots.add(root)


def migrate_legacy_voice_dirs() -> dict:
    """Move every folder still named after its voice id to the opaque name.

    Idempotent: a folder already in the new shape (its own `voice.json`
    names a voice_id, and hashing that id reproduces the folder's own name)
    is left untouched, so calling this twice - or on every launch, which is
    what actually happens via `_ensure_migrated` - does nothing the second
    time.

    Safe to interrupt: the id is written into `voice.json` and that write
    is flushed to disk BEFORE the rename is attempted. A crash between the
    two leaves an old-named folder whose `voice.json` already carries the
    id - which is exactly the state the next run resumes from, reading the
    id back out of the file instead of re-deriving it from the (still old)
    folder name. The rename itself is a same-volume `Path.rename`, which the
    filesystem performs as one atomic operation - there is no window where a
    folder exists under both names, or under neither.
    """
    root = refs_dir()
    if not root.is_dir():
        return {"migrated": [], "skipped": []}
    migrated: list[str] = []
    skipped: list[str] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if secure_delete.is_redirected(child):
            # The same refusal every other sweep in this module makes: a
            # reparse point is not walked into, migrated or not.
            skipped.append(child.name)
            continue
        meta = _read_meta(child)
        voice_id = meta.get("voice_id")
        if not isinstance(voice_id, str) or not VOICE_ID.match(voice_id):
            # No id recorded yet: this IS the legacy shape, where the folder
            # name always WAS the id.
            candidate = child.name
            if not VOICE_ID.match(candidate):
                # Not a slug this module ever produced (a stray folder, or an
                # already-opaque one that lost its voice.json some other
                # way) - nothing safe to derive it from. Leave it alone.
                skipped.append(child.name)
                continue
            voice_id = candidate
            meta["voice_id"] = voice_id
            _write_meta(child, meta)
        target_name = _hash_name(voice_id)
        if child.name == target_name:
            continue  # already in final form
        target = root / target_name
        if target.exists():
            # Something is already sitting where this voice belongs. Do not
            # guess which copy is right - the same choice save_upload makes
            # for a clip that will not die: destroying either one is the
            # wrong direction to fail in.
            # `child.name` is NOT logged here, and that is the whole point of
            # this line's shape. The only folders this loop ever sees in
            # legacy form are the ones named before the hash existed, and
            # there the folder name IS the slug of the label the user typed -
            # an on-screen name, outside the vault, in plaintext, at WARNING
            # level. The opaque target name says which voice this is just as
            # well for diagnosis and gives nothing away. The AST scanner in
            # tests/log_leak_scan.py could not have caught this: it follows
            # denylisted variable NAMES, and `child.name` is an attribute.
            logger.warning(
                "tts: cannot migrate a legacy voice folder - %s already exists",
                target_name)
            skipped.append(child.name)
            continue
        child.rename(target)
        migrated.append(voice_id)
    return {"migrated": migrated, "skipped": skipped}


def _voice_dir(voice_id: str) -> Path:
    if not VOICE_ID.match(voice_id or ""):
        # Anything that is not a plain slug is refused rather than
        # sanitised, so there is no path to smuggle into the hash input.
        raise RefError(TTS_REFERENCE_INVALID, "not a usable voice id")
    _ensure_migrated()
    return refs_dir() / _hash_name(voice_id)


def _read_meta(folder: Path) -> dict:
    try:
        with open(folder / META_NAME, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:                           # noqa: BLE001
        return {}


def _write_meta(folder: Path, data: dict) -> None:
    """Write voice.json, and make sure it is ON DISK before returning.

    The flush/fsync is not housekeeping. migrate_legacy_voice_dirs() writes
    the id into voice.json FIRST and renames the folder SECOND, and its
    docstring sells that order as crash-safe: the id is meant to be durable
    before the only thing that still carries it (the legacy folder name) goes
    away. Without the fsync that promise was a hope - the rename can reach the
    disk while the file contents are still in the page cache, and a power loss
    in between leaves a folder in its new opaque name holding an empty or
    stale voice.json. list_voices() then skips it and _voice_dir() cannot find
    it: the voice is gone from the app while every one of its files is still
    sitting there.
    """
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / META_NAME, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())


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
    # Before the folder rename this fed the directory NAME straight into
    # describe(voice_id) - the name was the id. It no longer is, so migrate
    # first and then read each folder's OWN voice.json for the id it belongs
    # to; a folder with none recorded is skipped below, same as before.
    _ensure_migrated()
    out = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        meta = _read_meta(child)
        voice_id = meta.get("voice_id")
        if not isinstance(voice_id, str) or not voice_id:
            # No recorded id: a folder the user is still filling in (no
            # voice.json yet), or not one of ours. Either way there is
            # nothing here to list it under.
            logger.debug("tts: skipping a voice folder with no recorded id")
            continue
        try:
            out.append(describe(voice_id))
        except RefError:
            # A folder the user is still filling in should not break the list.
            logger.debug("tts: skipping unusable voice folder for %s", voice_id)
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
    # delete() forty lines below refuses a redirected folder. This did not,
    # and it deletes as well: every audio file already in the folder goes so
    # that one voice keeps one clip. Point that name at a music library and
    # replacing a clip took the library with it, then wrote the new recording
    # in its place.
    #
    # Refused outright rather than skipped, unlike the sweeps that run on
    # their own. This one is a person pressing upload, so it can be answered:
    # a silent half-success that writes their voice into somebody else's
    # folder is the worse outcome. Its own code too - nothing is wrong with
    # the clip, and tts_reference_invalid would send them off to re-record a
    # perfectly good take.
    #
    # lexists, not exists: a folder that is not there yet is the normal first
    # upload, and is_redirected fails closed on a missing path.
    if os.path.lexists(folder) and secure_delete.is_redirected(folder):
        raise RefError(TTS_REFERENCE_FOLDER_REDIRECTED,
                       "the folder for this voice leads somewhere else")
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
    #
    # Named with the HASH, not the raw id: this file sits directly under
    # voice/refs/ - exactly the level a folder rename closes the leak at -
    # for the length of one upload, and a crash before the cleanup below runs
    # (a power cut, not just an exception this function already catches)
    # would otherwise leave a label sitting in a filename at the one place
    # this whole change exists to keep clean.
    staged = refs_dir() / (".incoming-" + _hash_name(voice_id) + suffix)
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
        secure_delete.discard(staged)
        raise

    # A reference clip is the user's own recorded voice. Replacing it with a
    # plain unlink left the previous take on disk, so a person who re-recorded
    # BECAUSE the first take said something they did not want kept still had it.
    #
    # shred() ANSWERS, and here the answer decides whether the upload happens
    # at all. A clip that is open - the engine holding it mid-sentence, an
    # antivirus mid-scan - survives, and a survivor does not step politely
    # aside: the new file lands BESIDE it, and `_audio_in` takes the first
    # audio file in sorted order, so `ref.mp3` keeps beating `ref.wav`. The
    # transcript and the metadata written below would then describe take 2
    # while take 1 is what gets cloned - the exact contradiction the transcript
    # comment further down was written to prevent, arriving by another door,
    # and reported as a successful upload.
    #
    # So: refuse, and install nothing. With the one clip per voice this folder
    # is supposed to hold, that leaves the disk exactly as it was. If a earlier
    # half-failure left several, some may already be gone by the time a later
    # one sticks - but that folder was already broken, and destroying the
    # user's own copies is the direction this module is allowed to fail in.
    stuck = [old.name
             for old in list(folder.iterdir())
             if old.is_file() and old.suffix.lower() in AUDIO_SUFFIXES
             and not secure_delete.shred(old)]
    if stuck:
        secure_delete.discard(staged)
        raise RefError(TTS_REFERENCE_CLIP_STUCK,
                       "the clip already saved for this voice is still in use")
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
        # The words that were said, which belong to the user exactly like the
        # audio shredded a few lines above.
        secure_delete.discard(tpath)

    _write_meta(folder, {
        "voice_id": voice_id,
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
    if secure_delete.is_redirected(folder):
        logger.warning("voice %s is a redirected name - not deleted", voice_id)
        return False
    # rglob + a per-FILE redirect check is not a guard: a file reached through
    # a junction has an ordinary path of its own, so the check passes and the
    # overwrite lands outside this folder. The prune has to happen at the
    # ancestor, which is what shred_tree does.
    _, stuck, pruned = secure_delete.shred_tree(folder)
    if stuck:
        logger.warning("voice %s: %d file(s) could not be removed",
                       voice_id, len(stuck))
    if pruned:
        # rmtree would walk straight into what was just refused.
        logger.warning("voice %s contains a redirected folder - left in place",
                       voice_id)
        return False
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
