"""Static scan: does a logging call in THIS tree pass something that can
carry vault content or a name the app displays on screen?

The rule, settled 2026-08-20: elysium.log is plaintext, outside the vault,
and survives a lock. A numeric id (chat id, message id) is fine there - it is
the diagnostic value notebook_worker.py's own comment defends, and that is
accepted deliberately. Two things are not fine:

  * CONTENT - a message body, a note's text or evidence quote, a character's
    persona/description/greeting, a persona's text, a boundary's wording, an
    extraction prompt or reply, a provider error body that may quote the
    request, a TTS worker's own error text (an engine formats the text it was
    asked to speak into its exception, and that text is a model reply).
  * NAMES - anything a person can read on screen inside the app: a chat's
    title, a character's name, a persona's name. The test that governs it:
    "uygulamada gorunen isimler hicbir zaman disarda durmasin" - names shown
    in the app must never sit outside the vault.

THE SHAPES THIS CATCHES

1. A variable bound by `except ... as NAME:` reaching a logging call.
   `logger.warning("...: %s", exc)` looks harmless and can print a whole
   reply, because nothing stops the exception's message from being built out
   of user text. Four expressions are exempt, and only these four:
     * `type(exc)` and `type(exc).__name__` - the CLASS. A class object has
       no attribute that can produce the message its instance was given, so
       `_CONFLICT_CODES[type(exc)]` (a lookup into a fixed vocabulary) is as
       safe as printing the name.
     * `exc.reason` / `exc.code` - a fixed-vocabulary sanitized code, by
       convention of AttachmentError/OpenRouterError/WorkerFailure. Never
       `.args`, which is exactly where the raw message text lives.
     * `getattr(exc, "code", <literal>)` - the same rule spelled dynamically,
       and ONLY with a literal string naming one of those same attributes.
     * `len(exc...)` - a count is a number, and numbers are the thing the
       rule explicitly allows.
   Any other getattr on a tainted value stays flagged: this cannot evaluate
   the attribute name, so it refuses rather than guesses.

2. A value DERIVED from such a variable. `msg = str(exc)` then
   `logger.warning("%s", msg)` is the same leak one line apart, so
   assignments propagate the taint: any binding whose right-hand side would
   itself have been flagged makes its target flagged too. A rebinding to
   something clean clears it again.

3. A tainted value handed to a HELPER IN THE SAME MODULE that logs that
   parameter. `routers/tts_runtime.py` had exactly this: a module-level
   `_fail(exc)` logging `exc.detail`, called from `except WorkerFailure as
   exc:` in four different endpoints, several hundred lines away. Functions
   are scanned first to find WHICH of their parameters reach a logging call,
   then a call handing a tainted value into one of those parameters (by
   position or by keyword) is flagged at the CALL site, where the fix usually
   belongs.

4. A denylist of variable names this codebase actually binds displayable
   content or names to, wherever they appear (not only inside an except
   handler): see CONTENT_DENYLIST.

5. A traceback of a LIVE exception: `logger.exception(...)` or
   `exc_info=True` inside an except handler. Both serialise the exception's
   own message into the log, which is shape 1 by another route - no name of
   the exception appears in the source, so nothing above would see it. These
   are reported under their own kind (KIND_TRACEBACK), because a traceback is
   also the single most useful thing in a crash report and that trade is not
   this scanner's to make silently.

TWO TAINTS, NOT ONE, BECAUSE THEY SPREAD DIFFERENTLY

An exception spreads through anything: hand it to a function and what comes
back may be its message. A denylisted NAME does not work that way, and
pretending it did produced a measured false-positive cascade:
`cur = con.execute("INSERT ...", (chat_id, text, ...))` mentions `text`, so
`asst_msg_id = cur.lastrowid` looked content-bearing, and then so did every
`logger.info("... id=%d", asst_msg_id)` downstream - which is the exact value
the rule says belongs in the log. Passing content to a database is not the
same event as formatting it into a message. So content taint spreads only
through expressions that are BUILDING A STRING out of it (f-string,
concatenation, slice, `str()`, `.strip()`, ...), and exception taint spreads
through everything.

WHAT THIS CANNOT DO, said plainly because the sentence is the point

This reads source text with `ast`, which means it sees SHAPES, not values.
It is defeated by:
  * a helper in ANOTHER module. Shape 3 is resolved within one file only:
    `from .helpers import report; report(exc)` is invisible here, because
    nothing in this file's text says what `report` does. Following it would
    mean resolving imports, which is a different (and much less certain)
    program than this one.
  * a helper reached indirectly - stored in a dict, passed as a callback,
    called through an object this scanner cannot resolve to a def. Helper
    names are matched on their last component, so a method and a module
    function that share a name are not told apart (an over-approximation,
    which can only ever add a check).
  * `getattr(exc, "ar" + "gs")`. This does not READ the attribute; it flags
    the whole expression because `exc` appears in it. So the shape is caught,
    but by refusing to reason rather than by understanding, and a safe
    dynamic access gets flagged too. The only way past it is the literal
    form in shape 1.
  * content taint that leaves a string and comes back: `parts.append(text)`
    then `logger.info(" ".join(parts))` is not followed.
  * a denylisted name reused for something harmless, or a new
    content-bearing name this list does not yet know.
  * an ATTRIBUTE that carries the same thing a denylisted name would. The
    denylist matches bare names, so `logger.warning("%s", child.name)` in
    tts/refs.py's legacy migration is invisible here, and that one is a live
    example rather than a hypothetical: `child` is a not-yet-migrated voice
    folder, whose name IS the slug of the label the user typed, being logged
    at the one moment the migration is trying to take it off the disk.
    Matching `.name` in general would flag every `path.name` in the tree, so
    it is left uncaught and written down instead.
  * anything that leaves this tree by a route other than `logger` - a
    `print()`, a file written by hand, a message put on the wire.
That ceiling is why this exists beside code review and a careful read, not
instead of them - the same honesty test_egress_chokepoint.py applies to its
own regex doors.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass

#: Logging methods this scanner treats as reaching elysium.log.
_LOG_METHODS = frozenset({
    "debug", "info", "warning", "error", "exception", "critical",
})

#: What a logger is called at the call site. `logger` is the module-level one
#: every file in this tree declares; `log` is the parameter name used by the
#: handful of functions that take a logger as an argument
#: (`worker_client._log_worker_event(log, ...)`, `secure_delete`,
#: `browser_profile`, `win_hardening`). The first version of this scanner
#: excluded `log` to avoid noise from test fixtures, and the cost of that was
#: that four shipped modules logged through a name it could not see. Tests are
#: out of the swept scope now, so the reason is gone and the coverage is not.
_LOGGER_NAMES = frozenset({"logger", "log"})

#: Module aliases for the `logging` module itself, for the shortcut form
#: `logging.warning(...)` that logs through the root logger.
_LOGGING_MODULE_NAMES = frozenset({"logging", "_logging"})

#: The factory call that returns a logger inline: `logging.getLogger(__name__)`.
#: Matched by the called NAME, so `_logging.getLogger(...)` (config.py imports
#: it under that alias) is covered too.
_LOGGER_FACTORY = "getLogger"

#: Attribute names that, by convention in THIS codebase, hold a sanitized,
#: fixed-vocabulary code rather than free text - AttachmentError.reason,
#: OpenRouterError.reason, WorkerFailure.code/.reason. Deliberately NOT
#: `.args`: that is exactly where Python puts the raw message a plain
#: `raise ValueError(...)` was given, which is the thing this whole scanner
#: exists to keep out. Deliberately NOT `.detail` either: `TtsError.detail`
#: and `ProvisionJob.detail` are free text by design, and one name cannot
#: mean "sanitized" in one class and "anything at all" in the next.
_SAFE_EXC_ATTRS = frozenset({"reason", "code"})

#: Builtins that reduce anything at all to a number. A count is not a name
#: and not content; the rule allows numbers out of the vault.
_NUMERIC_BUILTINS = frozenset({"len", "id", "hash", "int", "float", "round"})

#: Variable names this codebase actually binds displayable content or a
#: user/model-visible name to. Anywhere one of these reaches a logging call's
#: arguments, flagged - regardless of whether it is inside an except handler.
CONTENT_DENYLIST = frozenset({
    "content", "text", "description", "greeting", "display_name",
    "persona_block", "system_block", "char_row", "persona_row", "card_row",
    "phi", "prompt", "reply", "evidence", "wording", "user_message_text",
    # Added when this gate widened past its first four files:
    # `dropped_samples` is literally `text.strip()[:80]` of a line the queue
    # refused to speak (tts/speech_queue.py), and `voice_id` is the id of a
    # reference voice - opaque for anything minted since the voice folders
    # were hashed (the frontend mints a uuid now), but a slug of the label
    # the user typed for every voice created before that, and the two are
    # indistinguishable at a log site.
    "dropped_samples", "voice_id",
    # Added with the notebook grounding change. The scanner keys on the NAME
    # and does not propagate taint through a list comprehension, a
    # conditional expression or `next(...)`, so every one of these was
    # invisible while holding transcript text:
    #   `chunk_text`      the transcript window itself
    #   `haystacks`       every folded message body in it
    #   `folded_evidence` a quoted sentence somebody actually said
    #   `_body`           one whole message, unpacked from `haystacks`
    # The leading underscore on the last one is deliberate: it is the
    # discard half of a tuple unpack, and a name being ignored today is no
    # reason for the gate to stop seeing it.
    "chunk_text", "haystacks", "folded_evidence", "_body",
})

#: Calls that build a string out of what they are given. Used only to decide
#: whether CONTENT taint spreads through an assignment; see the module
#: docstring for the cascade that made the distinction necessary.
_STRINGY_CALLS = frozenset({"str", "repr", "format", "join", "strip", "lower",
                            "upper", "replace", "dumps", "escape", "title"})

#: Hit kinds. Separated because they are two different arguments: a raw value
#: reaching a logger is a mistake, while a traceback is a deliberate trade
#: somebody made for diagnosability and may make again.
KIND_CONTENT = "content"
KIND_TRACEBACK = "traceback"

#: Taint kinds. See the module docstring.
_EXC = "exc"
_TEXT = "text"


@dataclass(frozen=True)
class Hit:
    path: str
    lineno: int
    what: str
    kind: str = KIND_CONTENT


def _is_type_call(node: ast.AST) -> bool:
    """`type(x)` - the class object, which carries no instance message."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "type"
        and len(node.args) == 1
        and not node.keywords
    )


def _is_type_name(node: ast.AST) -> bool:
    """`type(x).__name__` - the class only, never the message."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "__name__"
        and _is_type_call(node.value)
    )


def _is_numeric_reduction(node: ast.AST) -> bool:
    """`len(text)` - a count, not the thing counted."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _NUMERIC_BUILTINS
        and len(node.args) == 1
    )


def _is_safe_exc_attr(node: ast.AST) -> bool:
    """`exc.reason` / `exc.code` - a fixed-vocabulary code, not a message."""
    return isinstance(node, ast.Attribute) and node.attr in _SAFE_EXC_ATTRS


def _is_safe_getattr(node: ast.AST) -> bool:
    """`getattr(exc, "code", <default>)` with a LITERAL attribute name in the
    same safe set as `_is_safe_exc_attr`.

    The literal is the whole condition. A computed name cannot be read here,
    and a scanner that shrugged at `getattr(exc, name)` would be handing out
    an exemption for the one form it understands least.
    """
    if not (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2):
        return False
    attr = node.args[1]
    if not (isinstance(attr, ast.Constant) and isinstance(attr.value, str)
            and attr.value in _SAFE_EXC_ATTRS):
        return False
    # A default of anything but a plain literal could itself be the leak.
    if len(node.args) >= 3 and not isinstance(node.args[2], ast.Constant):
        return False
    return True


def _is_blessed(node: ast.AST) -> bool:
    return (_is_type_call(node) or _is_type_name(node)
            or _is_numeric_reduction(node) or _is_safe_exc_attr(node)
            or _is_safe_getattr(node))


def _is_logger_receiver(node: ast.AST) -> bool:
    """Is `node` the thing a `.warning(...)` is being called ON, and is that
    thing a logger?

    Three shapes reach elysium.log and this had only ever recognised the first:

      logger.warning(...)                     a module-level name  (ast.Name)
      logging.getLogger(__name__).warning(..) a factory call       (ast.Call)
      logging.warning(...)                    the root shortcut    (ast.Name)

    The second shape was the hole. `_is_logger_call` required the receiver to
    be an `ast.Name`, so every inline `logging.getLogger(__name__).warning(...)`
    was skipped in silence - nine in the shipped tree, EIGHT in run_app.py and
    one in config.py (counted with ast, after a first draft of this comment
    said nine and one). Two of the eight pass `exc_info=True` inside an
    `except` block, so they were live traceback sites that never reached
    KNOWN_TRACEBACK_DEBT. Measured, not assumed: the scanner as it stood at
    commit d01bffa returns [] for run_app.py, and the widened one returns
    lines 216 and 579. Worse, run_app.py is named in _MUST_STAY_CLEAN, and it
    reported clean for the reason that eight of its ten logging calls were
    invisible to the thing certifying it.
    """
    if isinstance(node, ast.Name):
        return node.id in _LOGGER_NAMES or node.id in _LOGGING_MODULE_NAMES
    # `logging.getLogger(...)` / `_logging.getLogger(...)` / a bare
    # `getLogger(...)` imported directly. Matched on the called name, not on
    # the module it hangs off, so an alias cannot walk around this.
    if isinstance(node, ast.Call):
        return _called_name(node) == _LOGGER_FACTORY
    return False


def _is_logger_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and _is_logger_receiver(node.func.value)
        and node.func.attr in _LOG_METHODS
    )


def _called_name(node: ast.Call) -> str | None:
    """The bare name a call is made under: `f(x)` -> "f", `self._f(x)` ->
    "_f", `mod.f(x)` -> "f"."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _is_string_shaped(node: ast.AST) -> bool:
    """Is this expression building a string out of its parts?"""
    if isinstance(node, (ast.JoinedStr, ast.BinOp, ast.Subscript, ast.Name,
                         ast.Attribute, ast.FormattedValue)):
        return True
    if isinstance(node, ast.Call):
        name = _called_name(node)
        return name is not None and name in _STRINGY_CALLS
    return False


def _param_names(fn) -> list[str]:
    a = fn.args
    names = [p.arg for p in (*a.posonlyargs, *a.args, *a.kwonlyargs)]
    if a.vararg:
        names.append(a.vararg.arg)
    if a.kwarg:
        names.append(a.kwarg.arg)
    # `self` is the object, not something a caller hands a tainted value in.
    return [n for n in names if n not in ("self", "cls")]


@dataclass(frozen=True)
class _Sink:
    """A function in this module that logs some of its own parameters."""
    order: tuple[str, ...]          # parameter names, in positional order
    leaking: frozenset[str]         # the ones that reach a logging call


class _Scanner(ast.NodeVisitor):
    """Walks one module tracking which names are tainted at each point.

    A name is exception-tainted when bound by `except ... as NAME:`, when it
    is a seeded parameter (sink detection), or when assigned an expression
    that would itself have been flagged. It is content-tainted when assigned
    a STRING-BUILDING expression that mentions a denylisted name. Frames are
    pushed per function and per handler so taint never bleeds sideways into
    unrelated code.
    """

    def __init__(self, path: str, sinks: dict[str, _Sink] | None = None,
                 seed: tuple[str, ...] = ()) -> None:
        self.path = path
        self.sinks: dict[str, _Sink] = dict(sinks or {})
        self._frames: list[dict[str, str]] = [{n: _EXC for n in seed}]
        self._handler_depth = 0
        self.hits: list[Hit] = []

    # -- taint bookkeeping --------------------------------------------------

    def _names(self, *kinds: str) -> set[str]:
        out: set[str] = set()
        for frame in self._frames:
            out |= {n for n, k in frame.items() if k in kinds}
        return out

    def _bind(self, target: ast.AST, kind: str | None) -> None:
        for node in ast.walk(target):
            if isinstance(node, ast.Name):
                if kind is None:
                    for frame in self._frames:
                        frame.pop(node.id, None)
                else:
                    self._frames[-1][node.id] = kind

    def _kind_of(self, value: ast.AST) -> str | None:
        """What taint, if any, an assignment from `value` produces."""
        probe: list[Hit] = []
        self._check(value, 0, probe, denylist=False, taints=self._names(_EXC))
        if probe:
            return _EXC
        if _is_string_shaped(value):
            probe = []
            self._check(value, 0, probe, denylist=True,
                        taints=self._names(_TEXT))
            if probe:
                return _TEXT
        return None

    # -- structure ----------------------------------------------------------

    def _visit_function(self, node) -> None:
        self._frames.append({})
        self.generic_visit(node)
        self._frames.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        self._frames.append({})
        self._handler_depth += 1
        if node.name:
            self._frames[-1][node.name] = _EXC
        self.generic_visit(node)
        self._handler_depth -= 1
        self._frames.pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        kind = self._kind_of(node.value)
        for target in node.targets:
            self._bind(target, kind)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is None:
            return
        self.visit(node.value)
        self._bind(node.target, self._kind_of(node.value))

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.value)
        # `msg += str(exc)` only ever adds; it cannot clean.
        kind = self._kind_of(node.value)
        if kind is not None:
            self._bind(node.target, kind)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self._bind(node.target, self._kind_of(node.value))

    def visit_For(self, node: ast.For) -> None:
        self.visit(node.iter)
        # `for line in exc.args:` binds a piece of the message.
        self._bind(node.target, self._kind_of(node.iter))
        for child in (*node.body, *node.orelse):
            self.visit(child)

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self._bind(item.optional_vars,
                           self._kind_of(item.context_expr))
        for child in node.body:
            self.visit(child)

    def visit_Call(self, node: ast.Call) -> None:
        if _is_logger_call(node):
            for arg in (*node.args, *(kw.value for kw in node.keywords)):
                self._check(arg, node.lineno, self.hits)
            self._check_traceback(node)
        else:
            self._check_sink(node)
        self.generic_visit(node)

    def _check_sink(self, node: ast.Call) -> None:
        name = _called_name(node)
        sink = self.sinks.get(name) if name else None
        if sink is None:
            return
        via = f"the helper '{name}()', which logs that parameter"
        for i, arg in enumerate(node.args):
            if isinstance(arg, ast.Starred):
                continue
            if i < len(sink.order) and sink.order[i] in sink.leaking:
                self._check(arg, node.lineno, self.hits, via=via)
        for kw in node.keywords:
            if kw.arg is not None and kw.arg in sink.leaking:
                self._check(kw.value, node.lineno, self.hits, via=via)

    def _check_traceback(self, node: ast.Call) -> None:
        if not self._handler_depth:
            # Outside a handler there is no live exception to serialise.
            return
        why = None
        if node.func.attr == "exception":                    # type: ignore[union-attr]
            why = "logger.exception(...)"
        for kw in node.keywords:
            if kw.arg in ("exc_info", "stack_info"):
                if isinstance(kw.value, ast.Constant) and not kw.value.value:
                    continue
                why = f"{kw.arg}="
        if why is None:
            return
        self.hits.append(Hit(
            self.path, node.lineno,
            f"{why} inside an except handler writes the live exception's own "
            f"message into the log along with the traceback",
            KIND_TRACEBACK,
        ))

    # -- the rule -----------------------------------------------------------

    def _check(self, node: ast.AST, lineno: int, out: list[Hit],
               via: str = "", denylist: bool = True,
               taints: set[str] | None = None) -> None:
        # A blessed wrapper covers everything inside it - `type(exc).__name__`
        # touches `exc` but is exactly the form this codebase uses to say
        # "the class, never the message", so it does not recurse further.
        if _is_blessed(node):
            return
        if taints is None:
            taints = self._names(_EXC, _TEXT)
        if isinstance(node, ast.Name):
            tail = f", passed to {via}" if via else ""
            if node.id in taints:
                out.append(Hit(
                    self.path, lineno,
                    f"'{node.id}' (an exception bound by an except handler, "
                    f"or a value derived from one) reaches a logging call "
                    f"without going through type(...).__name__ or "
                    f".reason/.code{tail}",
                ))
                return
            if denylist and node.id in CONTENT_DENYLIST:
                out.append(Hit(
                    self.path, lineno,
                    f"'{node.id}' is on the content/name denylist and "
                    f"reaches a logging call{tail}",
                ))
                return
        for child in ast.iter_child_nodes(node):
            self._check(child, lineno, out, via, denylist, taints)


def _logging_sinks(tree: ast.AST, path: str) -> dict[str, _Sink]:
    """Which functions in THIS module log which of their own parameters.

    Run to a fixed point so a helper that calls a helper is found too. Each
    parameter is probed on its own: a function that logs one parameter is not
    thereby a hazard for the other five, and treating it as one flagged
    `_prepare_completion(user_message_text=...)` for a log line about an
    unrelated argument.
    """
    functions = [n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    sinks: dict[str, _Sink] = {}
    for _ in range(4):                      # depth cap, not a while True
        found: dict[str, _Sink] = dict(sinks)
        for fn in functions:
            params = _param_names(fn)
            if not params:
                continue
            leaking = set(found[fn.name].leaking) if fn.name in found else set()
            for param in params:
                if param in leaking:
                    continue
                probe = _Scanner(path, sinks=found, seed=(param,))
                for stmt in fn.body:
                    probe.visit(stmt)
                if any(h.kind == KIND_CONTENT for h in probe.hits):
                    leaking.add(param)
            if leaking:
                found[fn.name] = _Sink(tuple(params), frozenset(leaking))
        if found == sinks:
            break
        sinks = found
    return sinks


def scan_source(source: str, path: str = "<string>") -> list[Hit]:
    """Every hit in one module's source text, as a list (empty = clean)."""
    tree = ast.parse(source, filename=path)
    scanner = _Scanner(path, sinks=_logging_sinks(tree, path))
    scanner.visit(tree)
    return scanner.hits
