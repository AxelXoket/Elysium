"""tts/worker/_wire.py - the protocol, written down once.

The host lives in the app; each worker lives in the ENGINE's own interpreter
and cannot import the app package. So this module is deliberately stdlib-only
and gets reached two different ways:

    host    ->  from tts.worker import _wire            (normal package import)
    worker  ->  import _wire                            (its own directory is
                                                         on sys.path, because
                                                         it was run by path)

One file, so the two halves cannot drift apart into a protocol mismatch that
only shows up on a user's machine with a real engine installed.

FRAMING
    One JSON object per line, UTF-8, on stdout. Nothing else may EVER be
    written to stdout by a worker - torch, transformers and their friends print
    banners and progress bars, and a single stray byte desynchronises the
    stream. `claim_stdout()` below is the defence: it takes the real stdout
    away from the process and hands everything else stderr.

REQUESTS (host -> worker)      {"id": 1, "op": "load", ...}
RESPONSES (worker -> host)     {"id": 1, "ok": true, "result": {...}}
                               {"id": 1, "ok": false, "code": "...", "detail": "..."}
EVENTS (worker -> host, unsolicited, no id)
                               {"event": "progress", "stage": "compiling", "pct": 0.4}
                               {"event": "ready"}

DEATH
    The worker reads stdin. When the host dies its pipe closes, readline
    returns "" and the worker exits - so a crashed app cannot leave a process
    sitting on 8 GB of VRAM. This is belt; the job object in worker_client.py
    is braces.
"""
from __future__ import annotations

import json
import os
import sys

# ── ops ──────────────────────────────────────────────────────────────────────
OP_PING = "ping"
OP_LOAD = "load"
OP_SYNTHESIZE = "synthesize"
OP_TRANSCRIBE = "transcribe"       # V3-b: hear a reference clip's words
OP_PREPARE_REF = "prepare_ref"     # decode/resample/encode a reference voice
OP_SHUTDOWN = "shutdown"

# ── exit codes ───────────────────────────────────────────────────────────────
# Coarse on purpose: each one has to map to advice a person can act on.
EXIT_OK = 0
EXIT_CRASH = 1              # uncaught - we do not know what happened
EXIT_OOM = 2                # CUDA ran out of memory (an expected outcome)
EXIT_ENGINE_IMPORT = 3      # the environment is damaged, not the model
EXIT_MODEL_LOAD = 4         # the environment is fine, this model would not load

# ── the same words the app uses, so a worker can name its own failure ────────
CODE_OUT_OF_MEMORY = "tts_out_of_memory"
CODE_SYNTHESIS_FAILED = "tts_synthesis_failed"
CODE_REFERENCE_INVALID = "tts_reference_invalid"
CODE_TRANSCRIPT_REQUIRED = "tts_transcript_required"
CODE_WORKER_FAILED = "tts_worker_failed"

# -- what a worker is allowed to say in its own words ------------------------
#
# A `note` is the one field in the progress channel that is a SENTENCE rather
# than an identifier, and the host writes it into elysium.log - plaintext,
# beside the vault, surviving every lock. Free text from a worker cannot go
# there: an engine formats the sentence it was asked to speak, the path of
# the file it could not open, and the name of the reference clip into its own
# exception messages, and all three of those were measured arriving in the
# log.
#
# So the sentences live HERE, in the one module both halves import, and the
# host echoes a note only when it is one of them. Everything else is data and
# is sanitized like data. Adding a note means adding it to this list, which
# is the point: it makes "is this safe to write down" a decision somebody
# takes on purpose, once, instead of a property of whatever string happened
# to be in scope.
#
# NOT a formatting vocabulary. None of these takes a parameter, deliberately
# - the moment one interpolates, the thing it interpolates is untrusted again
# and the whole set is worth nothing.
NOTE_STAYING_BF16 = "staying bf16; generation will be slower"
NOTE_FIRST_COMPILE_SLOW = (
    "first compile is slow; a warm TORCHINDUCTOR_CACHE_DIR makes it "
    "~59s"
)
NOTE_COMPILING = "compiling the model for this GPU"
NOTE_EAGER_FALLBACK = (
    "falling back to eager decoding (triton-windows + MSVC?)"
)
NOTE_COMPILE_RETRY = "compiling failed; retrying without it"
NOTE_TEMP_COMPILE_CACHE = (
    "compiling into a temporary cache; every load will be slow"
)
NOTE_REBUILD_FROM_DISK = "the model will be rebuilt from disk instead"
NOTE_RESTORING_FROM_RAM = "restoring text2semantic from system memory"
NOTE_REBUILDING_FROM_DISK = "rebuilding the model from disk instead"
NOTE_FREED_FOR_DECODE = "the model was freed to let the last decode finish"
NOTE_LAZY_FIRST_SENTENCE = "the first spoken sentence will load it instead"
NOTE_FREEING_FOR_CODEC = "freeing text2semantic so the codec fits"
NOTE_REFERENCE_REENCODE = "the reference will be re-encoded next time"
NOTE_RECOMPILE_LONGER_CONTEXT = (
    "this request needs a longer context; recompiling once"
)
NOTE_DOES_NOT_FIT_CONTEXT = (
    "the request does not fit the chosen context window"
)
NOTE_LESS_CONTEXT_THAN_LIMIT = (
    "the text and reference leave less context than the length limit "
    "asks for"
)
NOTE_LENGTH_CAPPED = (
    "this text hit the length limit and was cut short - raise Max "
    "length, or say it in smaller pieces"
)
NOTE_RETIME_FAILED = (
    "the speaking-rate change failed; the sentence is spoken at its "
    "natural pace"
)

#: Every sentence above, for the host's membership test. Built from the
#: constants rather than retyped, so the two cannot disagree.
ALL_NOTES: frozenset = frozenset({
    NOTE_STAYING_BF16,
    NOTE_FIRST_COMPILE_SLOW,
    NOTE_COMPILING,
    NOTE_EAGER_FALLBACK,
    NOTE_COMPILE_RETRY,
    NOTE_TEMP_COMPILE_CACHE,
    NOTE_REBUILD_FROM_DISK,
    NOTE_RESTORING_FROM_RAM,
    NOTE_REBUILDING_FROM_DISK,
    NOTE_FREED_FOR_DECODE,
    NOTE_LAZY_FIRST_SENTENCE,
    NOTE_FREEING_FOR_CODEC,
    NOTE_REFERENCE_REENCODE,
    NOTE_RECOMPILE_LONGER_CONTEXT,
    NOTE_DOES_NOT_FIT_CONTEXT,
    NOTE_LESS_CONTEXT_THAN_LIMIT,
    NOTE_LENGTH_CAPPED,
    NOTE_RETIME_FAILED,
})

MAX_LINE_BYTES = 4 * 1024 * 1024   # a frame is small; anything larger is a bug


def encode(obj: dict) -> str:
    """One frame, one line. `ensure_ascii` keeps the pipe byte-safe whatever
    the console code page is - a Turkish sentence must survive the trip."""
    return json.dumps(obj, ensure_ascii=True, separators=(",", ":")) + "\n"


def decode(line: str) -> dict | None:
    """A frame, or None. Never raises: a desynchronised stream must degrade to
    "that line was noise", not take down the reader thread."""
    line = (line or "").strip()
    if not line or len(line) > MAX_LINE_BYTES:
        return None
    try:
        obj = json.loads(line)
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def ok(req_id, result: dict | None = None) -> dict:
    return {"id": req_id, "ok": True, "result": result or {}}


def err(req_id, code: str, detail: str = "") -> dict:
    return {"id": req_id, "ok": False, "code": code, "detail": detail}


def event(name: str, **fields) -> dict:
    return {"event": name, **fields}


# ── worker-side plumbing ─────────────────────────────────────────────────────

def claim_stdout():
    """Take the real stdout for the protocol and give everyone else stderr.

    Without this, the first library that prints a progress bar corrupts the
    frame stream and the host sees a hang it cannot explain. Returns the file
    object to write frames to; `sys.stdout` is left pointing at stderr so even
    a stray print() in engine code is harmless.
    """
    fd = os.dup(sys.stdout.fileno())
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    channel = os.fdopen(fd, "w", encoding="utf-8", newline="\n", buffering=1)
    sys.stdout = sys.stderr
    return channel


def serve(handler, channel=None, stdin=None) -> int:
    """Read frames until the host goes away, answering each with `handler`.

    `handler(op, payload) -> dict` returns the `result` body, or raises. The
    loop owns every failure mode so a worker script never has to remember to
    catch things: an OOM exits 2 (the host turns that into real advice), a
    handler error becomes one error frame and the worker stays alive for the
    next request.
    """
    channel = channel or claim_stdout()
    stdin = stdin or sys.stdin

    def send(obj):
        channel.write(encode(obj))
        channel.flush()

    send(event("ready", pid=os.getpid()))

    while True:
        line = stdin.readline()
        if line == "":
            return EXIT_OK          # the host closed the pipe: our cue to go
        req = decode(line)
        if req is None:
            continue                # noise, not a reason to die
        req_id = req.get("id")
        op = req.get("op") or ""
        if op == OP_SHUTDOWN:
            send(ok(req_id))
            return EXIT_OK
        try:
            send(ok(req_id, handler(op, req, send)))
        except _OomLike as exc:
            send(err(req_id, CODE_OUT_OF_MEMORY, str(exc)[:400]))
            return EXIT_OOM         # the CUDA context is not trustworthy now
        except WorkerError as exc:
            send(err(req_id, exc.code, exc.detail[:400]))
        except Exception as exc:    # noqa: BLE001 - report, do not vanish
            import traceback
            print(traceback.format_exc(), file=sys.stderr, flush=True)
            send(err(req_id, CODE_WORKER_FAILED, f"{type(exc).__name__}: {exc}"[:400]))


class WorkerError(Exception):
    """A failure with a name the app already knows how to say out loud."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(code)


class _OomLike(Exception):
    """Raised by engine halves when CUDA runs dry. Kept local so this module
    stays importable with no torch anywhere in sight."""


def oom(detail: str = "") -> _OomLike:
    return _OomLike(detail)


def is_oom(exc: BaseException) -> bool:
    """Recognise a CUDA OOM without importing torch. The class name is stable
    across versions; the message check catches the older RuntimeError form."""
    name = type(exc).__name__
    if name in ("OutOfMemoryError", "CudaOutOfMemoryError"):
        return True
    text = str(exc).lower()
    return "out of memory" in text or "cuda oom" in text
