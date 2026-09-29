"""A preset's regex scripts, run on the outgoing copy of the chat.

SillyTavern presets carry regex scripts (`extensions.regex_scripts`). Three
kinds exist, told apart by two flags:

  promptOnly    runs while the prompt is built; the stored message is untouched
  markdownOnly  runs on display only
  neither       edits the stored message for good when it is written

Only the first kind is applied here, and only when the user switched history
cleanup on for that preset. The third kind would change what the user wrote
or read, so it is never applied and the import report lists it. A script runs
on user messages when its placement has 1 and on replies when it has 2, and
only within its minDepth / maxDepth, counted as SillyTavern counts: the new
user message is depth 0, the reply before it depth 1.

Patterns are JavaScript literals ("/pattern/flags"), built and rewritten the
way js_regex describes, so Python matches what JavaScript matches; a pattern
it refuses is skipped and reported. Python's `re` cannot be interrupted, so the patterns
run in a child process that is killed when the turn's REGEX_TURN_BUDGET_S runs
out; the pattern that ran long is then skipped until restart, and the rest of
the chat goes out as it is. A repeat nested inside another repeat, or a
backreference, is refused at import already, and a message longer than
REGEX_INPUT_MAX_CHARS is left alone.

The replacement follows SillyTavern: `{{match}}` is the whole match, `$1` and
`$<name>` are groups with the script's trim strings removed, and `{{char}}`
and `{{user}}` in the script's own replacement text are filled in. Text taken
from the chat is never run through macros.

Nothing here logs text, patterns or script names.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass

try:  # Python 3.11+
    import re._parser as _sre_parse  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    import sre_parse as _sre_parse  # type: ignore[no-redef]

import card_macros

from . import js_regex

logger = logging.getLogger(__name__)

PLACEMENT_USER_INPUT = 1
PLACEMENT_AI_OUTPUT = 2
#: A message longer than this is sent without cleanup.
REGEX_INPUT_MAX_CHARS = 200_000
#: Seconds of regex work one turn may spend.
REGEX_TURN_BUDGET_S = 1.0
#: Pattern text longer than this is refused outright.
PATTERN_MAX_CHARS = 20_000

KIND_PROMPT = "prompt_only"
KIND_DISPLAY = "display_only"
KIND_PERMANENT = "edits_stored_message"
KIND_DISABLED = "disabled"

UNSUPPORTED_SYNTAX = js_regex.REASON_SYNTAX
UNSUPPORTED_FLAGS = js_regex.REASON_FLAGS
UNSUPPORTED_NESTED_REPEAT = "may_be_slow"
UNSUPPORTED_TOO_LONG = "pattern_too_long"


class UnsupportedPattern(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class Script:
    regex: re.Pattern
    global_: bool
    #: Match the text as UTF-16 code units (JavaScript without the u flag).
    utf16: bool
    replace: str
    trim: list[str]
    placement: frozenset[int]
    min_depth: int | None
    max_depth: int | None


def kind_of(script: dict) -> str:
    if script.get("disabled") is True:
        return KIND_DISABLED
    if script.get("promptOnly") is True:
        return KIND_PROMPT
    if script.get("markdownOnly") is True:
        return KIND_DISPLAY
    return KIND_PERMANENT


def prompt_scripts(scripts: list[dict]) -> list[dict]:
    """The scripts cleanup may run: prompt-only, not disabled, and placed on
    user input or model output."""
    return [s for s in scripts if kind_of(s) == KIND_PROMPT
            and _placement(s) & {PLACEMENT_USER_INPUT, PLACEMENT_AI_OUTPUT}]


def _placement(script: dict) -> set[int]:
    p = script.get("placement")
    return {x for x in p if isinstance(x, int) and not isinstance(x, bool)} \
        if isinstance(p, list) else set()


# ---------------------------------------------------------------------------
# The pattern: JavaScript semantics (js_regex), plus a screen for run time
# ---------------------------------------------------------------------------

def _max_of(av) -> int:
    return av[1]


def _has_repeat(items) -> bool:
    for op, av in items:
        name = str(op)
        if name in ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"):
            if _max_of(av) > 1:
                return True
            if _has_repeat(av[2]):
                return True
        elif any(_has_repeat(c) for c in _children(name, av)):
            return True
    return False


def _children(name: str, av):
    if name == "SUBPATTERN":
        return [av[3]]
    if name == "BRANCH":
        return list(av[1])
    if name in ("ASSERT", "ASSERT_NOT"):
        return [av[1]]
    if name == "ATOMIC_GROUP":
        return [av]
    if name == "GROUPREF_EXISTS":
        return [x for x in av[1:] if x is not None]
    return []


def _nested_repeat(items) -> bool:
    for op, av in items:
        name = str(op)
        if name in ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"):
            if _max_of(av) > 1 and _has_repeat(av[2]):
                return True
            if _nested_repeat(av[2]):
                return True
        elif any(_nested_repeat(c) for c in _children(name, av)):
            return True
        if name == "GROUPREF":
            return True
    return False


def compile_js(literal: str) -> js_regex.JsRegex:
    """The script's findRegex as SillyTavern builds it, for Python; or
    UnsupportedPattern with the reason the report shows."""
    if isinstance(literal, str) and len(literal) > PATTERN_MAX_CHARS:
        raise UnsupportedPattern(UNSUPPORTED_TOO_LONG)
    try:
        got = js_regex.parse(literal)
    except js_regex.Unsupported as exc:
        raise UnsupportedPattern(exc.reason) from None
    try:
        tree = _sre_parse.parse(got.regex.pattern)
    except (re.error, RecursionError):  # pragma: no cover - it compiled
        raise UnsupportedPattern(UNSUPPORTED_SYNTAX) from None
    if _nested_repeat(list(tree)):
        raise UnsupportedPattern(UNSUPPORTED_NESTED_REPEAT)
    return got


def _depth_limit(v, floor: int) -> int | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = int(v)
    return v if v >= floor else None


def prepare(scripts: list[dict]) -> list[Script]:
    """The runnable prompt scripts, in file order. Unsupported ones are
    skipped here and named in the import report."""
    out = []
    for s in prompt_scripts(scripts):
        try:
            js = compile_js(s.get("findRegex"))
        except UnsupportedPattern:
            continue
        trim = s.get("trimStrings")
        out.append(Script(
            regex=js.regex, global_=js.global_, utf16=js.utf16,
            replace=s.get("replaceString") if isinstance(s.get("replaceString"), str) else "",
            trim=[t for t in trim if isinstance(t, str) and t] if isinstance(trim, list) else [],
            placement=frozenset(_placement(s)),
            min_depth=_depth_limit(s.get("minDepth"), -1),
            max_depth=_depth_limit(s.get("maxDepth"), 0),
        ))
    return out


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------

_GROUP_REF = re.compile(r"\$(\d+)|\$<([^>]+)>")
_MATCH_MACRO = re.compile(r"\{\{match\}\}", re.IGNORECASE)


def _arg(m: re.Match, n: int, text: str, utf16: bool):
    """JavaScript's replace-callback argument n: the match, its groups, the
    offset (in UTF-16 units), the whole text, then the named groups."""
    count = (m.re.groups or 0)
    if n == 0:
        return m.group(0)
    if n <= count:
        return m.group(n)
    offset = m.start() if utf16 else len(text[:m.start()].encode("utf-16-le")) // 2
    rest = [offset, text] + ([m.groupdict()] if m.re.groupindex else [])
    return rest[n - count - 1] if n - count - 1 < len(rest) else None


def substitute(pattern: re.Pattern, global_: bool, template: str,
               trims: list[str], text: str, utf16: bool = True) -> str:
    """One script over one text, as SillyTavern's runRegexScript does it.
    `template` already has {{match}} as $0 and its own macros filled;
    captured chat text is inserted as it is. A value JavaScript reads as
    false (a group that did not take part, an offset of 0) inserts
    nothing."""
    work = js_regex.to_units(text) if utf16 else text

    def repl(m: re.Match) -> str:
        def one(g: re.Match) -> str:
            if g.group(1) is not None:
                v = _arg(m, int(g.group(1)), work, utf16)
            else:
                v = (m.groupdict().get(g.group(2)) if m.re.groupindex else None)
            if not v:
                return ""
            if isinstance(v, dict):
                return "[object Object]"
            if not isinstance(v, str):
                return str(v)
            for t in trims:
                v = v.replace(t, "")
            return v
        return _GROUP_REF.sub(one, template)

    out = pattern.sub(repl, work, count=0 if global_ else 1)
    return js_regex.from_units(out) if utf16 else out


# ---------------------------------------------------------------------------
# The worker process: the only way to stop a regex that runs too long
# ---------------------------------------------------------------------------
#
# Python's `re` holds the interpreter lock for the whole match and cannot be
# interrupted from another thread, so a pattern with catastrophic
# backtracking would freeze every request in the process. The patterns run
# in one child process instead; a job that is not back by the turn's
# deadline gets the child killed, and that pattern is not tried again until
# the app restarts.

def _worker_main(conn) -> None:  # pragma: no cover - runs in the child
    cache: dict[tuple[str, int], re.Pattern] = {}
    conn.send(("ready", None))
    while True:
        try:
            job = conn.recv()
        except (EOFError, OSError):
            return
        if job is None:
            return
        src, flags, global_, utf16, template, trims, text = job
        try:
            rx = cache.get((src, flags))
            if rx is None:
                rx = cache[(src, flags)] = re.compile(src, flags)
            conn.send(("ok", substitute(rx, global_, template, trims, text, utf16)))
        except Exception as exc:  # noqa: BLE001 - reported as a failed job
            conn.send(("error", type(exc).__name__))


def _kill_with_parent(pid: int):
    """Tie the child's life to this process: a Windows job object that kills
    its members when its last handle closes. The handle is only ever held
    here, so when this process ends in any way (a crash, a kill from the Task
    Manager) the OS closes it and the child goes too. Without this, a child
    stuck in a runaway pattern when the app died kept a CPU core busy
    forever, because it never reads the closed pipe.

    Returns the job handle, which the caller keeps for the child's life, or
    None where it cannot be set up (not Windows, or the call failed); then
    the child still stops on the closed pipe once its current job ends.
    """
    import sys

    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class _Basic(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _Io(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _Extended(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _Basic),
            ("IoInfo", _Io),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kill_on_job_close = 0x2000
    extended_limit_information = 9
    process_set_quota, process_terminate = 0x0100, 0x0001

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = _Extended()
    info.BasicLimitInformation.LimitFlags = kill_on_job_close
    proc = None
    try:
        if not kernel32.SetInformationJobObject(
                job, extended_limit_information, ctypes.byref(info),
                ctypes.sizeof(info)):
            raise OSError(ctypes.get_last_error())
        proc = kernel32.OpenProcess(process_set_quota | process_terminate,
                                    False, pid)
        if not proc or not kernel32.AssignProcessToJobObject(job, proc):
            raise OSError(ctypes.get_last_error())
    except OSError:
        kernel32.CloseHandle(job)
        logger.warning("The history cleanup worker is not tied to the app.")
        return None
    finally:
        if proc:
            kernel32.CloseHandle(proc)
    return job


def _close_handle(handle) -> None:
    if handle is None:
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle(handle)


#: How long the child may take to start. Not charged to any pattern: a slow
#: start (a cold disk, a virus scan of the exe) is not the pattern's fault.
WORKER_START_TIMEOUT_S = 20.0


class _Worker:
    def __init__(self) -> None:
        self.mu = threading.Lock()
        self._proc = None
        self._conn = None
        self._job = None
        #: (pattern, flags) that ran past a deadline once, on a running child.
        self.too_slow: set[tuple[str, int]] = set()

    def _spawn(self):
        import multiprocessing

        ctx = multiprocessing.get_context("spawn")
        parent, child = ctx.Pipe()
        proc = ctx.Process(target=_worker_main, args=(child,), daemon=True,
                           name="prompt-regex")
        proc.start()
        child.close()
        self._job = _kill_with_parent(proc.pid)
        return proc, parent

    def _start(self) -> float | None:
        """Start the child and wait until it is ready. The seconds it took,
        or None when it did not come up."""
        t0 = time.perf_counter()
        self._proc, self._conn = self._spawn()
        try:
            ready = (self._conn.poll(WORKER_START_TIMEOUT_S)
                     and self._conn.recv()[0] == "ready")
        except (EOFError, OSError):
            ready = False
        if not ready:
            self.stop()
            logger.warning("The history cleanup worker did not start.")
            return None
        return time.perf_counter() - t0

    def stop(self) -> None:
        proc, conn = self._proc, self._conn
        self._proc = self._conn = None
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass
        if proc is not None:
            if proc.is_alive():
                proc.kill()
            proc.join(timeout=2)
        job, self._job = self._job, None
        _close_handle(job)

    def alive(self) -> bool:
        return self._proc is not None and self._proc.is_alive()

    def run(self, script: Script, template: str, trims: list[str],
            text: str, clock: list[float]) -> str | None:
        """The substituted text, or None when the turn's deadline (`clock[0]`)
        passed (the child is killed) or the job failed. Starting the child
        moves the deadline by the time the start took."""
        key = (script.regex.pattern, script.regex.flags)
        if key in self.too_slow:
            return None
        left = clock[0] - time.perf_counter()
        if left <= 0 or not self.mu.acquire(timeout=left):
            return None
        try:
            if not self.alive():
                self.stop()
                took = self._start()
                if took is None:
                    return None
                clock[0] += took
            self._conn.send((script.regex.pattern, script.regex.flags,
                             script.global_, script.utf16, template, trims, text))
            left = max(0.0, clock[0] - time.perf_counter())
            if not self._conn.poll(left):
                self.too_slow.add(key)
                self.stop()
                logger.warning("A history cleanup pattern ran out of time "
                               "and is skipped until restart.")
                return None
            status, value = self._conn.recv()
        except (EOFError, OSError):
            self.stop()
            return None
        finally:
            self.mu.release()
        return value if status == "ok" else None


_worker = _Worker()


def stop_worker() -> None:
    """Stop the child process (app shutdown, tests)."""
    with _worker.mu:
        _worker.stop()


def _in_depth(script: Script, depth: int) -> bool:
    if script.min_depth is not None and depth < script.min_depth:
        return False
    return not (script.max_depth is not None and depth > script.max_depth)


def clean_chat(history: list[dict], user_text: str, scripts: list[Script], *,
               char: str, user: str, budget_s: float | None = None) -> str:
    """Run the scripts over the outgoing copy: `history` entries have their
    `content` replaced in place (the caller's copy, never the stored row), and
    the cleaned new user message is returned. Once the turn's budget is
    spent, the rest goes out as it is."""
    if not scripts:
        return user_text
    clock = [time.perf_counter() + (REGEX_TURN_BUDGET_S if budget_s is None
                                    else budget_s)]
    prepared = [(s, card_macros.render_macros(
                    _MATCH_MACRO.sub("$0", s.replace), char=char, user=user),
                 [card_macros.render_macros(t, char=char, user=user)
                  for t in s.trim]) for s in scripts]
    n = len(history)
    out_user = user_text
    for depth, msg in [*((n - k, history[k]) for k in range(n)), (0, None)]:
        role = "user" if msg is None else msg.get("role")
        text = out_user if msg is None else msg.get("content")
        if not isinstance(text, str) or len(text) > REGEX_INPUT_MAX_CHARS:
            continue
        want = PLACEMENT_USER_INPUT if role == "user" else (
            PLACEMENT_AI_OUTPUT if role == "assistant" else None)
        if want is None:
            continue
        for s, template, trims in prepared:
            if want not in s.placement or not _in_depth(s, depth):
                continue
            got = _worker.run(s, template, trims, text, clock)
            if got is not None:
                text = got
        if msg is None:
            out_user = text
        else:
            msg["content"] = text
    return out_user


def report(scripts: list[dict]) -> dict:
    """What the import report says about the scripts: names per kind, and
    the prompt-only ones that cannot run here with the reason."""
    out = {KIND_PROMPT: [], KIND_DISPLAY: [], KIND_PERMANENT: [],
           KIND_DISABLED: [], "unsupported": []}
    for s in scripts:
        name = s.get("scriptName") if isinstance(s.get("scriptName"), str) else ""
        kind = kind_of(s)
        out[kind].append(name)
        if kind == KIND_PROMPT:
            try:
                compile_js(s.get("findRegex"))
            except UnsupportedPattern as exc:
                out["unsupported"].append({"name": name, "reason": exc.reason})
    return out
