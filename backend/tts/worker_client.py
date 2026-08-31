"""tts/worker_client.py - own an engine subprocess without ever orphaning it.

The child holds several GB of VRAM. An orphan is not an untidy loose end; it is
a machine the user has to reboot. So this module is written against the ways
that actually happens on Windows, each of which was measured rather than
assumed:

  * `Popen.kill()` IS `Popen.terminate()` on Windows - one process, no
    descendants. Killing the interpreter we spawned does not promise to kill
    what IT spawned. -> every child is assigned to a JOB OBJECT with
    KILL_ON_JOB_CLOSE, so the whole tree dies together, and dies even if the
    app is terminated without running any Python at all.
  * `taskkill /F /T` walks parent-pid links in a live snapshot: if the middle
    process already exited, the grandchild is invisible and survives. It is a
    fallback here, never the mechanism.
  * The Windows pipe buffer is 4096 bytes. Anything that stops draining stdout
    or stderr deadlocks a worker that is holding the card - so both are drained
    by dedicated threads for the process's whole life, and stdin is written by
    a third thread, because writing to a child that is busy loading a model
    blocks the caller just as hard.
  * A redirected child's stdout defaults to the ANSI code page (cp1254 on this
    machine), not UTF-8. One accented byte in a library warning would raise
    inside the drain thread, stop the draining, and hang the worker. -> the
    child is forced to UTF-8 and the parent decodes with errors="replace".
  * `select()` cannot poll pipes on Windows at all. Reading with a timeout is
    the queue+thread pattern or nothing.
  * A thread blocked in pipe readline() cannot be interrupted from Python. It
    ends when the pipe closes. So it is a daemon, there is exactly one per
    pipe for the process lifetime, and shutdown closes the pipes after the
    kill rather than hoping.

Nothing here imports torch. This process never will.
"""
from __future__ import annotations

import ctypes
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path

import launch_token

from .errors import (
    ALL_CODES,
    TTS_LOAD_TIMEOUT,
    TTS_OUT_OF_MEMORY,
    TTS_RUNTIME_BROKEN,
    TTS_WORKER_CRASHED,
    TTS_WORKER_FAILED,
    TTS_WORKER_UNAVAILABLE,
)
from .worker import _wire

logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"
CREATE_NO_WINDOW = 0x08000000

# Keep the tail of stderr so a crash can be explained instead of shrugged at.
STDERR_KEEP_LINES = 120

# How a worker's exit code becomes something a person can act on. Exit 2 is
# special-cased in _death_reason: it is also CPython's own "bad command line /
# can't open file" code, so it only counts as OOM when something else - an OOM
# error frame, or OOM text on stderr - corroborates it. Telling someone to
# lower their memory settings because a file was missing would be a wild-goose
# chase with our name on it.
EXIT_CODE_MAP = {
    _wire.EXIT_OOM: TTS_OUT_OF_MEMORY,
    # The environment is damaged, not the model - "set up voice again" is the
    # fix, and saying "crashed" would send the user hunting the wrong thing.
    _wire.EXIT_ENGINE_IMPORT: TTS_RUNTIME_BROKEN,
    _wire.EXIT_MODEL_LOAD: TTS_WORKER_FAILED,
}

# Secrets and telemetry switches that must never reach an engine subprocess.
# The engines are a pile of third-party code; the deal is that they run LOCAL.
#
# ELYSIUM_LAUNCH_TOKEN is on this list and it is the most important entry.
# The launch token exists precisely to stop another process on this machine
# from reading the conversation over HTTP - and `env = dict(os.environ)` was
# handing it to the one subprocess this app spawns that runs somebody else's
# code. A compromised engine dependency, or a model checkpoint that gets
# execution during load, could have read it out of its own environment and
# asked the local API for everything, with the vault unlocked by definition.
_ENV_STRIP = ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_TOKEN",
              "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "WANDB_API_KEY",
              launch_token.ENV_VAR)
_ENV_FORCE = {
    # Forced, not setdefault: an inherited HF_HUB_OFFLINE=0 from the user's
    # shell would silently re-enable Hub access inside the worker.
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "GRADIO_ANALYTICS_ENABLED": "False",
    "WANDB_MODE": "disabled",
    "DO_NOT_TRACK": "1",
}


def _log_worker_event(log, engine_id: str, frame: dict) -> None:
    """One line per worker frame, at a level that matches what it means.

    A `note` is the worker telling the USER something ("every load will be
    slow"), so it is a warning; a bare stage is progress. Without this the
    whole progress channel was write-only.
    """
    if frame.get("event") != "progress":
        return
    # EVERY FIELD HERE CROSSED THE PIPE, so every field here is data.
    #
    # This function wrote `note` and `detail` verbatim, and the invariant
    # `WorkerFailure` states four hundred lines down - "No text a worker sent
    # is ever placed in it verbatim" - is the invariant it broke. Measured
    # arriving in elysium.log from shipped emitters: the sentence being
    # spoken (a stretch failure quotes it), a Windows path and therefore the
    # account name (an OSError message carries it), and the name of a
    # reference clip, which for any voice made before the folder names went
    # opaque is the label the user typed on screen. That last one is the
    # leak `tts/refs.py:_handle` was written to close, reopened one file
    # away.
    #
    # `sanitize_worker_detail` was already here, already used on the failure
    # path, and its own docstring says guarding at the boundary "protects
    # every consumer there will ever be, including the ones nobody has
    # written yet". This is one of those consumers.
    stage = describe_unknown_code(
        frame.get("stage") or frame.get("event") or "?")
    # A note is echoed only if it is one OUR OWN worker files chose. See
    # `_wire.ALL_NOTES`: seventeen fixed sentences, no interpolation. Any
    # other string in that field is an engine's free text and is treated as
    # what it is.
    raw_note = frame.get("note")
    if isinstance(raw_note, str) and raw_note in _wire.ALL_NOTES:
        note = raw_note
    elif raw_note:
        note = sanitize_worker_detail(raw_note)
    else:
        note = None
    detail = frame.get("detail")
    detail = sanitize_worker_detail(detail) if detail else None
    prefix = f"tts[{engine_id or '?'}]"
    # BOTH, when both are there. `note` is what to tell the user; `detail` is
    # the exception that caused it - and dropping the detail whenever a note
    # existed threw away exactly the line needed to diagnose the note.
    if note and detail:
        log.warning("%s %s: %s (%s)", prefix, stage, note, detail)
    elif note:
        log.warning("%s %s: %s", prefix, stage, note)
    elif detail:
        log.info("%s %s (%s)", prefix, stage, detail)
    else:
        log.info("%s %s", prefix, stage)


#: What an unreadable worker fault becomes. One fixed string, so a reader who
#: sees it knows the worker said something and this process refused to repeat
#: it, rather than wondering whether the field was simply empty.
WORKER_FAULT_UNCLASSIFIED = "worker fault: unclassified"

#: A Python exception class name, and nothing else that looks like language.
#: One identifier, no spaces, ending in one of the suffixes an exception class
#: actually uses. Anchored at both ends: a sentence cannot match this, and a
#: single word out of a reply that happened to match would still be one
#: CamelCase token carrying no name and no clause.
_CLASS_TOKEN = re.compile(
    r"^[A-Z][A-Za-z0-9_]{2,46}"
    r"(?:Error|Exception|Failure|Interrupt|Timeout|Abort|Exit)$")

#: How many leading tokens of a worker's detail are looked at. Both worker
#: formats put the class name at the FRONT (`_wire.serve` sends
#: `f"{type(exc).__name__}: {exc}"`, fish_s2 sends
#: `f"{what}: {type(exc).__name__}: {exc}"`), so a short prefix is all that is
#: needed - and it means a class-shaped word buried in a long reply, far past
#: where a class name could legitimately be, is never picked up.
_DETAIL_TOKEN_SCAN = 8

#: A contract code as this codebase spells one: snake_case, no punctuation.
_CODE_SHAPE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


def sanitize_worker_detail(detail: object) -> str:
    """Reduce a worker's own error text to a fixed-vocabulary fault line.

    THE TEXT IS INSPECTED AND DROPPED. What comes out is either
    `WORKER_FAULT_UNCLASSIFIED` or "worker fault: " plus one token that has
    the shape of a Python exception class - which is the fact this codebase
    has already decided is safe to log (`type(exc).__name__` is the approved
    escape hatch in tests/log_leak_scan.py, and _death_reason below has kept
    its own detail coarse for the same reason since it was written: "raw
    stderr can contain the text being spoken").

    Sanitizing HERE rather than at the log line is the whole point. `detail`
    has three consumers already - the log in routers/tts_runtime._fail,
    TtsHost._error_detail, and the /tts/state body built from it - and two of
    them are in files this change does not own. A guard at one log line
    protects one log line; a guard at the boundary where untrusted text
    enters the process protects every consumer there will ever be, including
    the ones nobody has written yet.

    Residual, stated rather than hidden: a reply consisting of exactly one
    CamelCase word ending in "Error", inside the first few tokens of an
    engine's error text, would be echoed. It is not a name, not a sentence,
    and not distinguishable from the class name it is imitating.
    """
    if not isinstance(detail, str) or not detail:
        return WORKER_FAULT_UNCLASSIFIED
    tokens = re.split(r"[^A-Za-z0-9_]+", detail)
    for token in tokens[:_DETAIL_TOKEN_SCAN]:
        if _CLASS_TOKEN.match(token):
            return f"worker fault: {token}"
    return WORKER_FAULT_UNCLASSIFIED


def describe_unknown_code(code: object) -> str:
    """A worker's unrecognised code, if it even looks like one.

    The code crossed the same process boundary as the detail and is data in
    exactly the same way, so it is not logged raw either - but a snake_case
    identifier is a contract code, not language, and dropping it would leave
    "the worker sent something wrong" with no way to find out what.
    """
    if isinstance(code, str) and _CODE_SHAPE.match(code):
        return code
    return "non-conforming"


class WorkerFailure(Exception):
    """A worker-side failure carrying a code the frontend already knows.

    THE INVARIANT ON `detail`: it is always a string THIS process composed. No
    text a worker sent is ever placed in it verbatim. `detail` reaches
    `TtsHost._error_detail` (and from there the /tts/state body the UI polls)
    and `routers/tts_runtime._fail`'s log line, which means elysium.log -
    plaintext, beside the vault, surviving every lock. A worker's own error
    text cannot go there: an engine formats the sentence it was asked to speak
    into its exception, and that sentence is a model reply. Frames arriving
    from the worker go through `_failure_from`, which is the only place that
    reads a frame's `detail` and the only place that has to hold this line.

    `reason` is the same string under the name this codebase uses everywhere
    for "sanitized, fixed vocabulary" (AttachmentError.reason,
    OpenRouterError.reason). Log sites should read `.reason`, so that what a
    reader sees at the log line is the promise itself rather than a field
    called `detail` that they have to go and verify.
    """

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(code)

    @property
    def reason(self) -> str:
        """The sanitized detail. A property, not a copy: a second field would
        go stale the first time somebody assigned to `detail`."""
        return self.detail


def _failure_from(frame: dict) -> WorkerFailure:
    """The ONE way an error frame becomes a WorkerFailure.

    Both fields of the frame are treated the same way, because they arrived
    the same way: `code` was already checked against the contract vocabulary
    before this existed ("it is data, not gospel"), and `detail` now gets the
    matching treatment it was missing. Building the exception here rather than
    at the raise site is what makes the invariant on WorkerFailure.detail
    checkable by reading one function.
    """
    code = frame.get("code")
    if code not in ALL_CODES:
        # An unknown string would reach the frontend and fall through to the
        # generic toast.
        logger.warning("tts: worker sent unknown code (%s)",
                       describe_unknown_code(code))
        code = TTS_WORKER_FAILED
    return WorkerFailure(code, sanitize_worker_detail(frame.get("detail")))


# ── the job object ───────────────────────────────────────────────────────────

class _JobObject:
    """A kernel-enforced "these processes die with me".

    ctypes restype/argtypes are declared explicitly and that is NOT decoration:
    without them ctypes assumes a C int return and TRUNCATES the 64-bit HANDLE
    on x64. Every later call then gets a garbage handle, the assignment quietly
    targets nothing, and orphan protection is gone with no error anywhere. It is
    the single most common reason hand-rolled job-object code "sometimes" fails.
    """

    def __init__(self) -> None:
        self._handle = None
        if not IS_WINDOWS:
            return
        try:
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateJobObjectW.restype = wintypes.HANDLE
            k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
            k32.SetInformationJobObject.restype = wintypes.BOOL
            k32.SetInformationJobObject.argtypes = [
                wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
            k32.AssignProcessToJobObject.restype = wintypes.BOOL
            k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            k32.OpenProcess.restype = wintypes.HANDLE
            k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            k32.TerminateJobObject.restype = wintypes.BOOL
            k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            k32.CloseHandle.restype = wintypes.BOOL
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            self._k32 = k32

            # NULL security attributes => a NON-inheritable handle. That matters:
            # if a child inherited a copy, the job would stay open after we let
            # go and KILL_ON_JOB_CLOSE would never fire.
            handle = k32.CreateJobObjectW(None, None)
            if not handle:
                return

            class _BASIC(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                ]

            class _IO(ctypes.Structure):
                _fields_ = [("ReadOperationCount", ctypes.c_uint64),
                            ("WriteOperationCount", ctypes.c_uint64),
                            ("OtherOperationCount", ctypes.c_uint64),
                            ("ReadTransferCount", ctypes.c_uint64),
                            ("WriteTransferCount", ctypes.c_uint64),
                            ("OtherTransferCount", ctypes.c_uint64)]

            class _EXT(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", _BASIC),
                            ("IoInfo", _IO),
                            ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]

            info = _EXT()
            info.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
            ok = k32.SetInformationJobObject(
                handle, 9, ctypes.byref(info), ctypes.sizeof(info))  # 9 = ExtendedLimit
            if not ok:
                k32.CloseHandle(handle)
                return
            self._handle = handle
        except Exception:                       # noqa: BLE001
            # No job object is a degraded mode, not a dead one - stdin-EOF and
            # terminate() still apply. Log loudly; this is our best guarantee.
            logger.warning("tts: job object unavailable; relying on stdin EOF", exc_info=True)
            self._handle = None

    @property
    def active(self) -> bool:
        return self._handle is not None

    def assign(self, pid: int) -> bool:
        if self._handle is None:
            return False
        # PROCESS_SET_QUOTA | PROCESS_TERMINATE
        proc = self._k32.OpenProcess(0x0100 | 0x0001, False, pid)
        if not proc:
            return False
        try:
            return bool(self._k32.AssignProcessToJobObject(self._handle, proc))
        finally:
            self._k32.CloseHandle(proc)

    def terminate(self) -> None:
        if self._handle is None:
            return
        try:
            self._k32.TerminateJobObject(self._handle, 1)
        except Exception:                       # noqa: BLE001
            pass

    def close(self) -> None:
        if self._handle is None:
            return
        try:
            self._k32.CloseHandle(self._handle)
        finally:
            self._handle = None


# ── pending requests ─────────────────────────────────────────────────────────

#: How often the wait loop re-checks the silence budget. Small enough that a
#: wedged worker still dies promptly, large enough to cost nothing.
_POLL_S = 0.5


@dataclass
class _Pending:
    done: threading.Event = field(default_factory=threading.Event)
    frame: dict | None = None
    #: Bumped by every progress frame while this request is in flight. The
    #: timeout is measured from HERE, not from the send - see request().
    last_progress: float = field(default_factory=time.monotonic)


class WorkerClient:
    """One engine subprocess and the only safe way to talk to it."""

    def __init__(self, python: str, script: str, *, engine_id: str = "",
                 env: dict | None = None, cwd: str | None = None):
        self.python = str(python)
        self.script = str(script)
        self.engine_id = engine_id
        self._env = env or {}
        self._cwd = cwd
        self._proc: subprocess.Popen | None = None
        self._job = _JobObject()
        self._pending: dict[int, _Pending] = {}
        self._pending_lock = threading.Lock()
        self._next_id = 0
        self._stderr_tail: list[str] = []
        self._ready = threading.Event()
        self._dead = threading.Event()
        self._exit_code: int | None = None
        self._outbox: queue.Queue = queue.Queue()
        self._events: list[dict] = []
        self._threads: list[threading.Thread] = []
        self.closing = False             # a deliberate close() has begun
        # One round trip at a time. The worker answers strictly in order, so a
        # second concurrent request would spend its whole timeout budget just
        # queueing behind the first - and then hard-kill a healthy worker for
        # being busy. Serialising makes the timeout measure WORK, not the queue.
        self._turn = threading.Lock()
        self._saw_oom = False

    # -- lifecycle ----------------------------------------------------------
    def start(self, timeout: float = 30.0) -> None:
        """Spawn and wait for the worker to say hello."""
        if self._proc is not None:
            raise WorkerFailure(TTS_WORKER_FAILED, "already started")
        if not Path(self.python).is_file():
            # Pointing the loader at a ghost interpreter is a runtime problem,
            # not a crash - and it has a one-click fix.
            raise WorkerFailure(TTS_RUNTIME_BROKEN, "interpreter is missing")

        env = dict(os.environ)
        # The worker runs a stack of third-party engine code. It gets no
        # credentials and no way to phone home - stripping here means every
        # engine inherits the guarantee instead of each remembering to.
        for key in _ENV_STRIP:
            env.pop(key, None)
        env.update(self._env)
        # LAST, on purpose. These went in before the per-worker overrides once,
        # which made the guarantee advisory: any caller that passed an env dict
        # could hand the engine HF_HUB_OFFLINE=0 back. No caller does today, and
        # that is exactly the kind of fact that stops being true quietly.
        env.update(_ENV_FORCE)
        # Both are needed. Without them the child's redirected stdio falls back
        # to the ANSI code page and a single accented byte in a warning kills
        # the drain thread, which deadlocks a worker holding the GPU.
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"

        flags = CREATE_NO_WINDOW if IS_WINDOWS else 0
        try:
            self._proc = subprocess.Popen(
                [self.python, "-u", self.script],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                bufsize=1, creationflags=flags, cwd=self._cwd, env=env,
            )
        except OSError as exc:
            # The class, not the message. This detail travels to the same two
            # places every other one does (the /tts/state body and
            # elysium.log), and WorkerFailure's invariant is only worth
            # anything if it holds on every path. Almost nothing is lost:
            # for an OSError the class IS the errno - FileNotFoundError,
            # PermissionError, OSError - and what the OS wrote around it
            # ("The system cannot find the file specified") adds a sentence
            # and a filesystem path to a fact already stated.
            raise WorkerFailure(
                TTS_WORKER_FAILED,
                f"could not spawn the worker: {type(exc).__name__}")

        # Assign immediately. There is a small window between spawn and
        # assignment; we accept it here rather than hand-rolling CreateProcessW
        # with CREATE_SUSPENDED, because the child does nothing but import in
        # that window and stdin-EOF still covers us.
        if not self._job.assign(self._proc.pid):
            logger.warning("tts: could not assign worker %s to a job object",
                           self._proc.pid)

        self._spawn_thread(self._read_stdout, "tts-worker-stdout")
        self._spawn_thread(self._read_stderr, "tts-worker-stderr")
        self._spawn_thread(self._write_stdin, "tts-worker-stdin")

        if not self._ready.wait(timeout):
            self.close()
            raise WorkerFailure(TTS_LOAD_TIMEOUT, "worker never became ready")
        # The wait can be satisfied by DEATH: _read_stdout sets _ready in its
        # finally so a start() is never left hanging on a worker that already
        # exited. A worker that died before saying hello is a failed start, and
        # its exit code is the diagnosis (3 = the environment is damaged) -
        # returning success here would throw that diagnosis away and let the
        # first request fail with a meaningless "unavailable".
        if self._dead.is_set() or not self.alive:
            reason = self._death_reason()
            self.close(grace=0)
            raise WorkerFailure(*reason)

    def _spawn_thread(self, fn, name: str) -> None:
        t = threading.Thread(target=fn, name=name, daemon=True)
        t.start()
        self._threads.append(t)

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # -- pipes --------------------------------------------------------------
    def _read_stdout(self) -> None:
        proc = self._proc
        try:
            for line in proc.stdout:                       # blocks until EOF
                # Guard PER FRAME: one malformed frame (say, an unhashable id)
                # must be dropped like any other noise. Letting it escape would
                # end this loop, and the finally below would then declare a
                # perfectly healthy worker dead.
                try:
                    frame = _wire.decode(line)
                    if frame is None:
                        continue                           # noise, not a frame
                    if "event" in frame:
                        self._on_event(frame)
                        continue
                    if frame.get("ok") is False and (
                            frame.get("code") == _wire.CODE_OUT_OF_MEMORY):
                        self._saw_oom = True               # corroborates exit 2
                    req_id = frame.get("id")
                    if not isinstance(req_id, int):
                        continue
                    with self._pending_lock:
                        pending = self._pending.get(req_id)
                    if pending is not None:
                        pending.frame = frame
                        pending.done.set()
                except Exception:                           # noqa: BLE001
                    logger.debug("tts: dropped a malformed frame", exc_info=True)
        except Exception:                                   # noqa: BLE001
            logger.debug("tts: stdout reader ended", exc_info=True)
        finally:
            # EOF on stdout arrives a hair BEFORE the process is reaped, so
            # poll() here can still say None. Reading the code at that instant
            # would map a CUDA OOM (exit 2) to a generic crash and lose the one
            # piece of advice that would have helped. Wait for the real code.
            try:
                proc.wait(timeout=5)
            except Exception:                               # noqa: BLE001
                pass
            # The worker is gone. Release every caller NOW with the real
            # reason: without this each one waits out its own full timeout
            # while the VRAM it was waiting for is already freed.
            self._exit_code = proc.poll()
            self._dead.set()
            self._ready.set()                              # unblock a start()
            with self._pending_lock:
                waiting = list(self._pending.values())
                self._pending.clear()
            for pending in waiting:
                pending.frame = None
                pending.done.set()

    def _read_stderr(self) -> None:
        proc = self._proc                     # survives close() nulling _proc
        try:
            for line in proc.stderr:
                self._stderr_tail.append(line.rstrip("\n"))
                if len(self._stderr_tail) > STDERR_KEEP_LINES:
                    del self._stderr_tail[:-STDERR_KEEP_LINES]
        except Exception:                                   # noqa: BLE001
            logger.debug("tts: stderr reader ended", exc_info=True)

    def _write_stdin(self) -> None:
        """Writes are queued rather than done inline: a child that is busy
        loading a model is not reading stdin, and 4096 bytes later the caller
        would block inside a request handler.

        The pipe is captured as a LOCAL at thread start. close() nulls
        `self._proc` before it queues the goodbye frame, so writing through the
        attribute would raise AttributeError right then - silently, into the
        except below - and no worker would ever get to exit gracefully.
        """
        stdin = self._proc.stdin
        try:
            while True:
                item = self._outbox.get()
                if item is None:
                    break
                stdin.write(item)
                stdin.flush()
        except Exception:                                   # noqa: BLE001
            logger.debug("tts: stdin writer ended", exc_info=True)

    def _on_event(self, frame: dict) -> None:
        name = frame.get("event")
        if name == "ready":
            self._ready.set()
        # A worker that is still reporting is still working. The load timeout
        # exists to catch a WEDGED worker, and charging a slow-but-progressing
        # compile against it made the documented cold path impossible: this
        # file's own header says the first inductor compile takes ~346 s while
        # TTS_LOAD_TIMEOUT_S is 180, so a cold cache could never finish a load -
        # every attempt was killed mid-compile, and voice only ever worked if
        # enough repeated failures happened to warm the cache by accident.
        if name == "progress":
            now = time.monotonic()
            with self._pending_lock:
                for pending in self._pending.values():
                    pending.last_progress = now
        # LOG, then buffer. The ring buffer had no accessor and no reader
        # anywhere, so all 29 _progress() emissions from the fish worker were
        # unreachable AND unlogged - including the ones a person can act on:
        # "first compile is slow; a warm TORCHINDUCTOR_CACHE_DIR makes it ~59s",
        # "compiling into a temporary cache; every load will be slow", "staying
        # bf16; generation will be slower", "falling back to eager decoding".
        # The user saw state "loading" for up to TTS_LOAD_TIMEOUT_S and then a
        # bare tts_load_timeout.
        _log_worker_event(logger, self.engine_id, frame)
        self._events.append(frame)
        if len(self._events) > 200:
            del self._events[:-200]

    @property
    def events(self) -> list[dict]:
        """The recent worker frames (newest last, capped at 200).

        Exposed so the buffer is readable at all - it was write-only, which is
        why nothing noticed the progress channel went nowhere.
        """
        return list(self._events)

    # -- requests -----------------------------------------------------------
    def request(self, op: str, payload: dict | None = None,
                timeout: float = 60.0) -> dict:
        """One round trip. Raises WorkerFailure with a contract code.

        Serialised: the worker answers strictly in order, so the timeout must
        not start ticking while a request is still queued behind another one.
        """
        with self._turn:
            return self._request_locked(op, payload, timeout)

    def _request_locked(self, op, payload, timeout) -> dict:
        if not self.alive:
            # If we watched it DIE, say why - the exit code is the diagnosis,
            # and "unavailable" would throw it away. A clean exit (0) is not a
            # death: that is a worker we closed ourselves.
            if self._exit_code is not None and self._exit_code != _wire.EXIT_OK:
                raise WorkerFailure(*self._death_reason())
            raise WorkerFailure(TTS_WORKER_UNAVAILABLE, "worker is not running")
        with self._pending_lock:
            self._next_id += 1
            req_id = self._next_id
            pending = _Pending()
            self._pending[req_id] = pending

        try:
            frame = {"id": req_id, "op": op, **(payload or {})}
            self._outbox.put(_wire.encode(frame))

            # `timeout` is a SILENCE budget, not a wall-clock one: it is
            # measured from the last progress frame. A worker that keeps
            # reporting is working, and the first inductor compile legitimately
            # runs far past any fixed budget (see _on_event). A worker that
            # stops reporting is caught in exactly the same time as before.
            while not pending.done.wait(_POLL_S):
                with self._pending_lock:
                    quiet = time.monotonic() - pending.last_progress
                if quiet >= timeout:
                    # A worker that stopped answering is not trustworthy: it
                    # may be mid-allocation on the card. Take it down rather
                    # than leave it.
                    self.close()
                    raise WorkerFailure(
                        TTS_LOAD_TIMEOUT,
                        f"{op} stopped responding for {int(quiet)}s",
                    )
                if not self.alive:
                    break

            # `frame`, not `reply`: this is the worker's answer frame, and
            # `reply` is the name this codebase uses for a MODEL reply - the
            # one thing that must never be logged (tests/log_leak_scan.py
            # denylists it by name and flagged this line for it).
            frame = pending.frame                # snapshot once
            if frame is None:                    # died while we waited
                if self.closing:
                    # WE ended it (unload, vault lock, shutdown) - the request
                    # was preempted, nothing crashed.
                    raise WorkerFailure(TTS_WORKER_UNAVAILABLE,
                                        "voice was shut down")
                raise WorkerFailure(*self._death_reason())
            if not frame.get("ok"):
                raise _failure_from(frame)
            return frame.get("result") or {}
        finally:
            with self._pending_lock:
                self._pending.pop(req_id, None)

    def _death_reason(self) -> tuple[str, str]:
        code = self._exit_code if self._exit_code is not None else (
            self._proc.poll() if self._proc else None)
        # Exit 2 is CPython's own usage-error code too; treat it as OOM only
        # when something corroborates it.
        if code == _wire.EXIT_OOM and not self._oom_corroborated():
            return TTS_WORKER_CRASHED, f"worker exited with code {code}"
        mapped = EXIT_CODE_MAP.get(code, TTS_WORKER_CRASHED)
        # The detail stays COARSE on purpose: raw stderr can contain the text
        # being spoken (engines log their input), and this string travels to
        # the UI and the log file. The full tail stays in memory on
        # `stderr_tail` for an explicit diagnostics view, never logged wholesale.
        detail = f"worker exited with code {code}"
        if mapped == TTS_OUT_OF_MEMORY:
            detail = "CUDA ran out of memory"
        return mapped, detail

    def _oom_corroborated(self) -> bool:
        if self._saw_oom:
            return True
        return any(_wire.is_oom(Exception(ln)) for ln in self._stderr_tail[-20:])

    @property
    def stderr_tail(self) -> list[str]:
        return list(self._stderr_tail)

    @property
    def exit_code(self) -> int | None:
        return self._exit_code

    # -- teardown -----------------------------------------------------------
    def close(self, grace: float = 3.0) -> None:
        """End the worker for certain, then let the threads retire.

        Order matters: ask nicely, then TerminateJobObject (which reaches every
        descendant), then terminate the process itself, then close the pipes -
        because a reader thread blocked in readline() only ends when its pipe
        closes.
        """
        # Raised BEFORE anything dies: a request preempted by this close must
        # read as "voice was shut down", never as a crash - a deliberate vault
        # lock that stamps tts_worker_crashed on the snapshot sends the user
        # hunting a failure that never happened (audit-2).
        self.closing = True
        proc, self._proc = self._proc, None
        if proc is None:
            self._job.close()
            return

        if grace > 0 and proc.poll() is None:
            try:
                self._outbox.put(_wire.encode({"id": 0, "op": _wire.OP_SHUTDOWN}))
                proc.wait(timeout=grace)
            except Exception:                               # noqa: BLE001
                pass

        if proc.poll() is None:
            self._job.terminate()               # the whole tree, atomically
            try:
                proc.terminate()                # on Windows this IS kill()
            except Exception:                   # noqa: BLE001
                pass
            try:
                proc.wait(timeout=5)
            except Exception:                   # noqa: BLE001
                logger.warning("tts: worker %s would not die", proc.pid)

        self._exit_code = proc.poll()
        self._outbox.put(None)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:                   # noqa: BLE001
                pass
        self._job.close()
        self._dead.set()

    def wait_dead(self, timeout: float = 5.0) -> bool:
        return self._dead.wait(timeout)


# ── process-wide teardown ────────────────────────────────────────────────────
# Registered by the host so the vault-lock path, the window-closed handler and
# atexit can all reach the live worker without importing the host.
_TEARDOWN: list = []


def register_teardown(fn) -> None:
    if fn not in _TEARDOWN:
        _TEARDOWN.append(fn)


def hard_close(grace: float = 1.0) -> None:
    """Kill anything voice has running. Safe to call any number of times, from
    any thread, including from a window-closed handler where blocking would
    freeze the UI - so it never raises and never waits long."""
    for fn in list(_TEARDOWN):
        try:
            fn(grace)
        except Exception:                       # noqa: BLE001
            logger.warning("tts: teardown hook failed", exc_info=True)
