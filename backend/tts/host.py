"""tts/host.py - the single load slot and everything that guards it.

The graphics card holds one model. Every rule here follows from that:

  * ONE slot. Loading a different model unloads the first; loading the same one
    is a no-op; a second load while one is in flight is REFUSED rather than
    queued, because a queue here means two resident models and a machine that
    needs a reboot.
  * REFUSE BEFORE SPAWNING. Preflight and the runtime check happen before any
    process exists, so "it will not fit" costs nothing and cannot half-happen.
  * NEVER BE SILENT. A worker that dies on its own must leave a visible reason.
    The user pressed speak and heard nothing; the app owes them the sentence
    that explains it. `error_code` on the snapshot is that sentence's name.
  * LET GO. The vault locking, the app closing, and a long silence all give the
    VRAM back - the last one because memory the user is not using should be
    theirs again.

State: unloaded -> loading -> loaded -> unloading -> unloaded, with `error` as
a terminal-until-next-attempt state that carries the reason.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path

import config
import secure_delete

from .base import DetectedModel
from .errors import (
    TTS_CACHE_OUTSIDE_DATA_DIR,
    TTS_MODEL_ALREADY_LOADING,
    TTS_RUNTIME_INSTALLING,
    TTS_RUNTIME_MISSING,
    TTS_WORKER_CRASHED,
    TTS_WORKER_FAILED,
    TTS_WORKER_UNAVAILABLE,
)
from .preflight import check_fit
from .worker import _wire
from .worker_client import WorkerClient, WorkerFailure, register_teardown
from . import runtimes

logger = logging.getLogger(__name__)

STATE_UNLOADED = "unloaded"
STATE_LOADING = "loading"
STATE_LOADED = "loaded"
STATE_UNLOADING = "unloading"
STATE_ERROR = "error"


def worker_script(engine_id: str) -> Path:
    """Where this engine's worker script lives, on disk, right now.

    In a frozen onefile build the bundled data files are extracted to
    `sys._MEIPASS` at launch, which gives us real paths - and we need real
    paths, because the script is run by an interpreter that is not ours and
    cannot see inside the exe.
    """
    base = getattr(sys, "_MEIPASS", None)
    if base and getattr(sys, "frozen", False):
        return Path(base) / "tts_worker" / f"{engine_id}.py"
    return Path(__file__).resolve().parent / "worker" / f"{engine_id}.py"


def _refuse_to_speak_outside_our_own_folder(cache: Path) -> None:
    """The spoken reply is chat content. It does not leave the app's folder.

    K-40. The junction guards elsewhere stopped this app DELETING somebody
    else's files; they did nothing about WRITING. So a user who moved the
    audio cache to another drive and left a junction behind kept getting their
    conversation written there in the clear - and none of the four sweeps that
    exist to remove it could reach the target, because every one of them now
    refuses a redirected name. Speech accumulated somewhere nothing would ever
    clean.

    The owner's rule, in their words: nothing that lives in the encrypted
    database may be written permanently anywhere else. A wav of the reply is
    exactly that content, read aloud.

    The test is "does this name lead somewhere else", not "is it under the
    data directory". abspath normalises without following links; resolve
    follows them. If the two disagree, something on this path is a reparse
    point - a junction, a symlink, a mount point - and it does not matter
    which, or whether it sits on the last component or an ancestor.

    Deliberately NOT a containment check against DATA_DIR. Moving the whole
    data folder with ELYSIUM_DATA_DIR is supported, coherent, and creates no
    redirection: the vault, the uploads and the audio all move together and
    every sweep still reaches them. What is refused is the arrangement where
    the audio alone points off into a folder the cleanup can no longer touch.
    """
    try:
        if Path(os.path.abspath(cache)) == cache.resolve():
            return
    except OSError:                                   # pragma: no cover
        return  # cannot tell; the deletion guards still hold
    raise WorkerFailure(
        TTS_CACHE_OUTSIDE_DATA_DIR,
        f"{cache} leads somewhere else on disk",
    )


class VoiceHost:
    """Owns at most one loaded model, and answers for it honestly."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._client: WorkerClient | None = None
        self._state = STATE_UNLOADED
        self._uid: str | None = None
        self._engine_id: str | None = None
        self._vram_mb: int | None = None
        self._error_code: str | None = None
        self._error_detail: str = ""
        self._last_used: float = 0.0
        self._busy = False               # a load is in flight
        self._inflight = 0               # requests currently inside the worker
        # Names of audio files the last wipe_cache() could not delete. Read by
        # on_vault_locked so /vault/lock can report a cleanup that only
        # partly happened instead of a flat ok.
        self._last_wipe_left: list[str] = []
        # (stage, note) pairs already handed to a client. The ring buffer keeps
        # its last 200 frames, so without this the same compile note would be
        # re-sent on every utterance for the life of the worker.
        self._notes_sent: set[tuple] = set()
        # Bumped by every unload/teardown. A load whose round trip began before
        # the bump must NOT publish its worker afterwards: "lock the vault while
        # a model loads" would otherwise finish the load into a locked app.
        self._generation = 0
        # Overridable so tests can point at a worker that needs no GPU.
        self.script_resolver = worker_script
        # NOTE: no per-instance teardown registration (audit-2: every VoiceHost
        # pinned itself into worker_client._TEARDOWN forever). The module-level
        # hook below reaches whichever host is current.

    # -- reporting ----------------------------------------------------------
    def take_notes(self) -> list[str]:
        """User-facing notes the worker has emitted since the last call.

        A `note` is the worker telling the PERSON something they can act on:
        "first compile is slow; a warm TORCHINDUCTOR_CACHE_DIR makes it ~59s",
        "staying bf16; generation will be slower", "falling back to eager
        decoding". All 29 of them landed in worker_client's ring buffer, which
        had no reader anywhere in tts/ or routers/ - so on a Windows box with
        no MSVC or triton, `compile_failed` fired on every single load, speech
        ran 2-3x slower forever, and the user was told nothing at all.

        Draining rather than peeking: each note is worth saying once. Returns
        [] when no worker is up, which is the common case and not an error.
        """
        with self._lock:
            client = self._client
        if client is None:
            return []
        out: list[str] = []
        for frame in client.events:
            note = frame.get("note")
            if not note:
                continue
            key = (frame.get("stage"), note)
            if key in self._notes_sent:
                continue
            self._notes_sent.add(key)
            out.append(str(note))
        return out

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "state": self._state,
                "uid": self._uid,
                "engine_id": self._engine_id,
                "vram_mb": self._vram_mb,
                "error_code": self._error_code,
                "error_detail": self._error_detail,
                "idle_seconds": (
                    round(time.monotonic() - self._last_used, 1)
                    if self._state == STATE_LOADED else None
                ),
            }

    def _fail(self, code: str, detail: str = "") -> WorkerFailure:
        """Remember a failure so it survives past the request that hit it - the
        UI polls, and the person who pressed the button may already be looking
        somewhere else."""
        with self._lock:
            self._error_code = code
            self._error_detail = detail[:400]
        return WorkerFailure(code, detail)

    def _clear_error(self) -> None:
        with self._lock:
            self._error_code = None
            self._error_detail = ""

    # -- loading ------------------------------------------------------------
    def load(self, model: DetectedModel, values: dict | None = None,
             timeout: float | None = None) -> dict:
        values = values or {}
        with self._lock:
            if self._busy:
                # Not queued on purpose: waiting would mean two models resident.
                raise self._fail(TTS_MODEL_ALREADY_LOADING,
                                 "another model is loading")
            if (self._state == STATE_LOADED and self._uid == model.uid
                    and self._client is not None and self._client.alive):
                self._last_used = time.monotonic()
                return self.snapshot()
            self._busy = True
            self._state = STATE_LOADING
            gen = self._generation
            # Remembered so a REFUSAL can put it back. The pre-spawn checks
            # below reject without touching the running worker, and wiping the
            # identity anyway left the app reporting "nothing loaded" while a
            # process still held its VRAM - invisible in the UI, and to anyone
            # wondering where the memory went.
            prior = (self._uid, self._engine_id, self._vram_mb,
                     self._client is not None and self._client.alive)
            # Publish WHICH model is coming up, immediately.
            #
            # The identity used to be written only on success, so for the whole
            # (long) load /tts/active could not match the requested uid and
            # answered "unloaded" - while the model was in fact filling the
            # card. Two things broke on that: every voice control stayed in its
            # ready face instead of saying "still loading", and the readiness
            # check counted our own in-flight allocation as somebody else's,
            # announcing "Not enough GPU memory to load this voice model"
            # about the load in progress. The restore below already puts the
            # previous identity back if this load is refused or fails.
            self._uid = model.uid
            self._engine_id = model.engine_id
            self._vram_mb = None

        client = None
        aborted = False
        try:
            # Both checks happen while no process exists, so a refusal is free
            # and cannot leave anything half-started.
            from . import provision as _provision

            if _provision.job(model.engine_id).get("running"):
                # A worker starting out of an environment that is being
                # rebuilt underneath it locks files mid-swap - the exact
                # chain audit-2 showed gutting a working install.
                raise self._fail(TTS_RUNTIME_INSTALLING,
                                 "engine setup is running")
            status = runtimes.status(model.engine_id)
            if status.state != "ready":
                raise self._fail(
                    status.error_code or TTS_RUNTIME_MISSING,
                    "the engine runtime is not ready",
                )
            fit = check_fit(model, values)
            if not fit.fits:
                raise self._fail(fit.reason or TTS_WORKER_UNAVAILABLE, fit.detail)

            self._drop_client("making room")

            client = self._start_worker(model, status.python)
            result = client.request(
                _wire.OP_LOAD,
                {
                    "model_path": model.path,
                    "engine_id": model.engine_id,
                    "variant": model.variant,
                    "values": values,
                    "cache_dir": str(config.TTS_CACHE_DIR),
                },
                timeout=timeout or float(config.TTS_LOAD_TIMEOUT_S),
            )
            with self._lock:
                if self._generation != gen:
                    # An unload / vault lock / shutdown happened while the
                    # round trip was in flight. Publishing now would resurrect
                    # a worker into an app that has already let go - possibly
                    # with the vault shut. The model loaded; it does not matter.
                    aborted = True
                else:
                    self._client = client
                    self._state = STATE_LOADED
                    self._uid = model.uid
                    self._engine_id = model.engine_id
                    self._vram_mb = result.get("vram_mb") or fit.estimate_mb
                    self._last_used = time.monotonic()
            if aborted:
                client.close(grace=0)
                with self._lock:
                    self._state = STATE_UNLOADED
                    self._uid = None
                    self._engine_id = None
                    self._vram_mb = None
                raise WorkerFailure(TTS_WORKER_UNAVAILABLE,
                                    "voice was shut down while the model loaded")
            self._clear_error()
            self._ensure_health_thread()
            return self.snapshot()
        except WorkerFailure as exc:
            # The client is LOCAL until published. Every failure path must end
            # it here - a worker with no reference in the app is invisible to
            # unload/lock/shutdown and sits on its VRAM until the machine
            # reboots. (Measured: three failed loads = three live workers.)
            if client is not None and client is not self._client:
                client.close(grace=0)
            if not aborted:
                with self._lock:
                    prior_uid, prior_engine, prior_vram, prior_alive = prior
                    if (prior_alive and self._client is not None
                            and self._client.alive):
                        # A refused load did not disturb what was already
                        # resident, so the state must keep describing it. The
                        # failure is still reported - it just stops pretending
                        # the previous model evaporated.
                        self._state = STATE_LOADED
                        self._uid = prior_uid
                        self._engine_id = prior_engine
                        self._vram_mb = prior_vram
                    else:
                        self._state = STATE_ERROR
                        self._uid = None
                        self._engine_id = None
                        self._vram_mb = None
                    if self._error_code is None:
                        self._error_code = exc.code
                        self._error_detail = exc.detail
            raise
        finally:
            with self._lock:
                self._busy = False

    def _start_worker(self, model: DetectedModel, python: str) -> WorkerClient:
        script = self.script_resolver(model.engine_id)
        if not Path(script).is_file():
            # The app shipped without this engine's worker, or the bundle was
            # tampered with. NOT tts_runtime_broken: that message tells the
            # user to run "Set up voice" again, which reinstalls the engine's
            # ENVIRONMENT - it cannot restore a file that ships inside the app,
            # so the advice would send them in a circle.
            raise self._fail(TTS_WORKER_FAILED,
                             f"worker script missing for {model.engine_id}")
        client = WorkerClient(python, str(script), engine_id=model.engine_id)
        try:
            client.start(timeout=float(config.TTS_HANDSHAKE_TIMEOUT_S))
        except WorkerFailure as exc:
            raise self._fail(exc.code, exc.detail)
        return client

    # -- speaking -----------------------------------------------------------
    def speak(self, text: str, values: dict | None = None,
              out_path: str | None = None, extra: dict | None = None,
              message_id: int | None = None) -> dict:
        with self._lock:
            client = self._client
            if client is None or not client.alive or self._state != STATE_LOADED:
                raise self._fail(TTS_WORKER_UNAVAILABLE, "no model is loaded")
        out = out_path or self._next_out_path(message_id)
        payload = {"text": text, "out": out, "values": values or {}}
        payload.update(extra or {})
        with self._lock:
            self._inflight += 1
            self._last_used = time.monotonic()   # busy counts as used
        try:
            result = client.request(
                _wire.OP_SYNTHESIZE, payload,
                timeout=float(config.TTS_SYNTH_TIMEOUT_S),
            )
        except WorkerFailure as exc:
            self._note_failure(exc)
            raise
        finally:
            with self._lock:
                self._inflight -= 1
                self._last_used = time.monotonic()
        self._clear_error()
        return result

    def request(self, op: str, payload: dict, timeout: float | None = None) -> dict:
        """Escape hatch for the other worker ops (transcribe, prepare_ref)."""
        with self._lock:
            client = self._client
            if client is None or not client.alive:
                raise self._fail(TTS_WORKER_UNAVAILABLE, "no model is loaded")
            self._inflight += 1
            self._last_used = time.monotonic()
        try:
            result = client.request(op, payload,
                                    timeout=timeout or float(config.TTS_SYNTH_TIMEOUT_S))
        except WorkerFailure as exc:
            self._note_failure(exc)
            raise
        finally:
            with self._lock:
                self._inflight -= 1
                self._last_used = time.monotonic()
        return result

    def _note_failure(self, exc: WorkerFailure) -> None:
        with self._lock:
            if (exc.code == TTS_WORKER_UNAVAILABLE
                    and self._state in (STATE_UNLOADING, STATE_UNLOADED)):
                # The request lost a race with a deliberate unload/lock. The
                # caller still gets its exception; the SNAPSHOT stays clean -
                # a phantom "crashed" after a clean lock is a lie (audit-2).
                return
            self._error_code = exc.code
            self._error_detail = exc.detail
            if self._client is not None and not self._client.alive:
                self._state = STATE_ERROR
                self._uid = None
                self._vram_mb = None

    def _next_out_path(self, message_id: int | None = None) -> str:
        cache = Path(config.TTS_CACHE_DIR)
        cache.mkdir(parents=True, exist_ok=True)
        _refuse_to_speak_outside_our_own_folder(cache)
        self._trim_cache(cache)
        # The message id is IN THE NAME, and that is K-45. Without it there
        # was no way to answer "which of these files is that reply", so
        # deleting a message left its audio behind - the same words, in the
        # clear, next to an encrypted database that no longer holds them. And
        # if the user never spoke again the 30-minute trim never ran either,
        # so it sat there until the vault locked.
        #
        # 0 for the paths that have no message yet (a preview, a probe). Those
        # are still swept by age, by the lock and at launch.
        tag = message_id if isinstance(message_id, int) and message_id > 0 else 0
        # Monotonic-ish and collision-free without needing a clock the tests
        # would have to freeze.
        return str(cache / f"speak-{tag}-{int(time.time() * 1000)}"
                           f"-{threading.get_ident()}.wav")

    def forget_message_audio(self, message_id: int) -> list[str]:
        """Destroy the spoken form of one message. Returns what would not go.

        K-45. Deleting a message removed its row and its image bytes from the
        vault and left the wav of it on disk. The owner's rule is that content
        living in the encrypted database is not written permanently anywhere
        else, and a recording of a reply the user just deleted is the sharpest
        case of that: they deleted it BECAUSE they wanted it gone.
        """
        cache = Path(config.TTS_CACHE_DIR)
        if not cache.is_dir() or secure_delete.is_redirected(cache):
            return []
        left: list[str] = []
        for wav in cache.glob(f"speak-{int(message_id)}-*.wav"):
            if not secure_delete.shred(wav):
                left.append(wav.name)
        if left:
            logger.warning(
                "tts: %d audio file(s) for a deleted message could not be "
                "removed and are still readable on disk: %s",
                len(left), ", ".join(left))
        return left

    def _trim_cache(self, cache: Path) -> int:
        """Drop generated audio older than the retention window.

        wipe_cache is the only other deletion, and its only callers are the
        vault lock and shutdown (audit KÖK 10). A long day without locking
        therefore accumulated the WHOLE conversation as plaintext wav on disk -
        not permanently, but with no ceiling within the session, which is the
        same thing to anyone who leaves the app open.

        Age rather than count: a file is only useful while its message is on
        screen and replayable, and a count cap would evict a reply somebody is
        still listening to on a busy afternoon. Cheap enough to run per
        sentence - one listdir over a directory this pass keeps small.

        Returns how many were removed.
        """
        # is_dir() first, and it is not decoration: is_redirected fails
        # closed on any OSError, ENOENT included, so a cache folder that
        # simply does not exist yet answers True. Without this the guard
        # would fire on a fresh install where nobody has spoken yet.
        if cache.is_dir() and secure_delete.is_redirected(cache):
            # The same trap wipe_audio_cache refuses 250 lines below, and this
            # is the one that walked into it most often: once per synthesised
            # sentence rather than once per launch. Junction the cache folder
            # at somebody's Music library and their files aged past the
            # retention window were overwritten and unlinked, three or four
            # sentences into the first reply.
            #
            # Checking each FILE would not do it. A file reached THROUGH a
            # junction has an ordinary path of its own, so is_redirected says
            # False about it and shred goes ahead. Only the ancestor carries
            # the reparse point.
            #
            # Quiet on purpose, at debug: this runs per sentence, and a
            # warning here would be thousands of identical lines. The launch
            # sweep warns once, which is where a person sees it.
            logger.debug(
                "tts: the audio cache path is a redirected name - not trimmed")
            return 0
        cutoff = time.time() - float(config.TTS_CACHE_MAX_AGE_S)
        removed = 0
        try:
            entries = list(cache.iterdir())
        except OSError:
            return 0
        for path in entries:
            if not path.name.startswith("speak-") or path.suffix != ".wav":
                continue
            try:
                if path.stat().st_mtime >= cutoff:
                    continue
                if not secure_delete.shred(path):
                    continue
                removed += 1
            except OSError:
                # A file the player still holds open (Windows) is not an error:
                # it is this pass's business to be best-effort, and the next
                # one will get it.
                continue
        if removed:
            logger.info("tts: cleared %d expired audio file(s).", removed)
        return removed

    # -- letting go ---------------------------------------------------------
    def unload(self, reason: str = "") -> dict:
        with self._lock:
            # Always bump: an in-flight load checks this before publishing, and
            # "there was nothing loaded YET" is exactly the case that matters.
            self._generation += 1
            if self._client is None and self._state == STATE_UNLOADED:
                # Explicitly letting go also dismisses the last error - the
                # user has acknowledged it; keeping it would read as current.
                self._error_code = None
                self._error_detail = ""
                return self.snapshot()
            self._state = STATE_UNLOADING
        logger.info("tts: unloading voice model (%s)", reason or "no reason given")
        self._drop_client(reason)
        with self._lock:
            self._state = STATE_UNLOADED
            self._uid = None
            self._engine_id = None
            self._vram_mb = None
            self._error_code = None
            self._error_detail = ""
        # NO wipe_cache() here. Unloading is about VRAM; the audio cache is
        # about privacy, and the two have different lifetimes.
        #
        # This mattered most when an idle reaper came through here: a model
        # unloading after ten minutes deleted the wav the user was listening to
        # AT THAT MOMENT, the browser's in-flight request for the rest of the
        # file failed, and the sentence stopped mid-word with nothing to explain
        # it. The reaper is gone, but the separation stays - any future caller
        # of unload() inherits the same trap otherwise.
        #
        # The privacy promise is unchanged and still kept where it is actually
        # made: on_vault_locked() wipes explicitly (its own docstring - "no
        # audio of one should be left readable on disk"), and so does shutdown.
        # Those are the moments the session ends; an idle GPU is not one.
        return self.snapshot()

    def _drop_client(self, reason: str, grace: float = 2.0) -> None:
        with self._lock:
            client, self._client = self._client, None
        if client is not None:
            client.close(grace=grace)

    def wipe_cache(self) -> int:
        """Delete generated audio.

        A cache of wav files is the user's conversation in audible form, sitting
        in the clear next to a database that went to the trouble of being
        encrypted. It exists for the length of a session and no longer.

        The work lives in wipe_audio_cache() because the launch path needs the
        same deletion before any host exists. Two copies of a deletion this
        sensitive would drift.
        """
        removed, left = wipe_audio_cache()
        # The names, not the count. `removed` told the caller how well this
        # went and nothing about what is still lying around, so /vault/lock
        # could only ever answer {"ok": true} - while the user's conversation
        # stayed on disk in audible form beside a vault reporting itself
        # locked. `left` is the half a caller can act on.
        self._last_wipe_left = left
        return removed

    def on_vault_locked(self) -> list[str]:
        """While the vault is locked nothing may speak, no sentence of the
        user's should still be sitting in a child process, and no audio of one
        should be left readable on disk.

        Returns the audio files it could NOT delete - empty on the normal
        path. The caller is a route that promises the vault is now closed, and
        that promise has to be able to come back false.
        """
        try:
            self.unload("vault locked")
        except Exception:                       # noqa: BLE001
            logger.exception("tts: unload on vault lock failed")
        self._last_wipe_left = []
        self.wipe_cache()
        return list(self._last_wipe_left)

    def _teardown(self, grace: float = 1.0) -> None:
        """Called from atexit, the window-closed handler and the vault path.
        Must never block the UI, so the grace period is short and it is the
        job object that guarantees the rest."""
        with self._lock:
            self._generation += 1        # abort any load that is in flight
        self._drop_client("process teardown", grace=grace)
        with self._lock:
            self._state = STATE_UNLOADED
            self._uid = None
        # Closing the app ends the session, and the audio cache is the user's
        # conversation in audible form. It must not outlive the session just
        # because the exit path was the window button instead of the lock.
        self.wipe_cache()

    # -- health -------------------------------------------------------------
    def poll_health(self) -> dict:
        """Called on a timer and on every status read. Two jobs: notice a
        worker that died on its own, and give back memory nobody is using."""
        with self._lock:
            client = self._client
            state = self._state
            last_used = self._last_used

        if state == STATE_LOADED and client is not None and not client.alive:
            code, detail = client._death_reason()
            logger.warning("tts: worker died on its own (%s)", code)
            with self._lock:
                self._client = None
                self._state = STATE_ERROR
                self._uid = None
                self._vram_mb = None
                self._error_code = code or TTS_WORKER_CRASHED
                self._error_detail = detail
            # The process is dead but the CLIENT is not: it still holds the job
            # handle (and with it the reap-descendants guarantee), a blocked
            # stdin writer, and three pipes. Dropping the reference without
            # close() would leak all of that for the life of the app.
            client.close(grace=0)
            return self.snapshot()

        # NO idle unload. There used to be one here, and removing it is the
        # point rather than an oversight.
        #
        # A timer answering "has the user gone away?" is a guess, and it was
        # wrong in both directions: it reaped a model that costs 60-99 s to
        # rebuild while someone was still reading the reply, and it went on
        # holding the card for ten minutes after they really had left. The lock
        # says the same thing without guessing - it is an act, not an
        # inference - so `on_vault_locked` frees the card immediately and
        # unlocking loads it back. This method now only WATCHES health.
        return self.snapshot()

    def _ensure_health_thread(self) -> None:
        """A timer, because nobody else is obliged to call us.

        poll_health also runs on every /tts/state read, but a minimised window
        polls nothing - and "the VRAM comes back when you stop using it" must
        not depend on the UI being looked at. ONE module-level thread for the
        process (audit-2: a per-instance while-True thread outlived every
        host a test suite created), beating over whichever host is current.
        """
        _ensure_module_health_thread()


_HOST: VoiceHost | None = None
_HOST_LOCK = threading.Lock()
_HEALTH_STARTED = False
_TEARDOWN_HOOKED = False


def _current_host() -> VoiceHost | None:
    with _HOST_LOCK:
        return _HOST


def _module_teardown(grace: float = 1.0) -> None:
    """The ONE teardown hook (audit-2: per-instance registration pinned every
    VoiceHost a test created into worker_client._TEARDOWN forever). It reaches
    whichever host is current at call time."""
    host = _current_host()
    if host is not None:
        host._teardown(grace)


def _ensure_module_health_thread() -> None:
    global _HEALTH_STARTED
    with _HOST_LOCK:
        if _HEALTH_STARTED:
            return
        _HEALTH_STARTED = True
    def beat() -> None:
        # Short fixed ticks, interval re-read per tick: a long sleep would pin
        # whatever interval was configured when it STARTED, which is invisible
        # everywhere except a test that shrinks it and a user who grows it.
        waited = 0.0
        while True:
            time.sleep(0.25)
            waited += 0.25
            if waited < float(getattr(config, "TTS_HEALTH_POLL_S", 30) or 30):
                continue
            waited = 0.0
            host = _current_host()
            if host is None:
                continue
            try:
                host.poll_health()
            except Exception:                   # noqa: BLE001
                logger.exception("tts: health poll failed")

    threading.Thread(target=beat, name="tts-health", daemon=True).start()


def get_host() -> VoiceHost:
    global _HOST, _TEARDOWN_HOOKED
    with _HOST_LOCK:
        if _HOST is None:
            _HOST = VoiceHost()
        if not _TEARDOWN_HOOKED:
            # Once, for the process - not once per host instance.
            register_teardown(_module_teardown)
            _TEARDOWN_HOOKED = True
        return _HOST


def wipe_audio_cache() -> tuple[int, list[str]]:
    """Delete every generated wav. Returns (deleted, names it could not).

    The audio cache is the conversation in audible form, in the clear, beside
    a database that went to the trouble of being encrypted. It gets emptied on
    three edges now: the vault lock, shutdown, and - added later - launch.

    Launch is the one that closes the hole the other two left. Both of the
    original callers are graceful exits, so a crash, a kill, or a power cut
    left the spoken conversation on disk with nothing coming to remove it: the
    30-minute age trim only runs during the NEXT synthesis, which never
    happens if the user does not use voice again.

    Module level, not a method, because at launch there is no host yet and
    building one to delete files would start a health thread for nothing.
    """
    removed = 0
    left: list[str] = []
    try:
        cache = Path(config.TTS_CACHE_DIR)
        if secure_delete.is_redirected(cache):
            # Same trap the browser purge and the vault discard already refuse.
            # This was the one deletion left that walked straight through it:
            # junction the cache folder at somebody's Music library and every
            # launch swept it. Reproduced before this line existed.
            logger.warning(
                "tts: the audio cache path is a redirected name - not swept")
            return 0, []
        for wav in cache.glob("*.wav"):
            if secure_delete.is_redirected(wav) or secure_delete.is_shared(wav):
                left.append(wav.name)
                continue
            try:
                if not secure_delete.shred(wav):
                    raise OSError("not removed")
                removed += 1
            except OSError:
                # On Windows a wav the browser is still streaming (or a worker
                # still holds) raises PermissionError, and skipping it
                # silently left the user's spoken conversation sitting in the
                # clear while the vault showed locked - the exact thing the
                # caller's docstring promises does not happen.
                left.append(wav.name)
    except Exception:                           # noqa: BLE001
        logger.warning("tts: could not wipe the audio cache", exc_info=True)
    if left:
        # Same standard as _rekey_sidecars: a file we failed to secure MUST be
        # named, because it is exactly the one the promise did not cover.
        logger.warning(
            "tts: %d audio file(s) could not be deleted and are still "
            "readable on disk: %s", len(left), ", ".join(left[:5]),
        )
    return removed, left
