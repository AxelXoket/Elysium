"""What the backend can put in a `detail` field or an SSE `code` field.

A census of the error VOCABULARY. Not a behaviour test, and not a substitute
for one.

It exists because the thing it replaces, `test_error_vocabulary.py`'s two
regexes, could only ever see a string literal. Measured on 2026-08-10: that
regex found 51 codes; this finds the rest. The ones it missed were not exotic.
`proxy_health.py:101` relays six reasons through `health.get("reason")`, and
`routers/models_router.py:41` relays six more through a bare `reason` name.
Neither had been counted anywhere, by anything, ever.

THE PROPERTY THAT MAKES THIS A FORCING FUNCTION

A detail argument this module cannot resolve is a hard FAILURE, not a skip.
That single inversion is the whole design. The regex's fatal flaw was never
that it was a regex; it was that a computed detail produced *nothing* rather
than an error, so a blind spot looked exactly like a clean bill of health. Here
an unreadable site is a red test naming the file, the line and the exact source
text, and the author closes it by writing a literal or by declaring the
alphabet in the module that owns the site.

TRIED AND REJECTED

*A wider regex over the SSE dict shape.* The two sites that matter,
`completions.py` around 1646 and 1681, build the code from a variable. A regex
sees the names `code` and `detail` and never the alphabet behind them.

*Observing what the app emits at runtime, instead of reading it.* Three
separate design studies proposed it and all three die on the same two facts.
First, the full suite only ever constructs a subset of the vocabulary, so
"everything catalogued must have been observed" is red on day one for the
remainder, and the only cure is to mark them exempt, which is the hole the
exercise exists to close. Second, and decisively: `completions.py` wraps its
whole streaming generator in a bare `except Exception` that answers with
`internal_error`. An assertion raised from inside the emit path is swallowed by
that handler and rewritten into a cataloged code, so the alarm arrives as a
green record. Reading the source cannot be fooled that way.
"""

from __future__ import annotations

import ast
import importlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

BACKEND_DIR = str(Path(__file__).resolve().parent.parent)
_BACKEND = Path(BACKEND_DIR)
_SKIP_DIRS = {"tests", "verify", ".venv", "__pycache__", "dist", "build",
              "node_modules", "avatars"}

#: The SSE `type` values that carry a user-facing code, and the channel name
#: each one is reported under. `voice_chunk` and `voice_done` are absent
#: deliberately: they carry audio and completion metadata, not error codes.
_SSE_TYPES = {
    "error": "sse",
    "voice_error": "voice_sse",
    "notice": "notice",
    "voice_notice": "voice_sse",
}


@dataclass(frozen=True)
class Emission:
    code: str
    channel: str            #: http | sse | voice_sse | notice
    status: int | None      #: only ever set at a literal raise site
    file: str               #: posix, relative to backend/
    line: int


@dataclass(frozen=True)
class Unresolved:
    file: str
    line: int
    channel: str
    source: str             #: the exact expression text, verbatim


#: Sites whose code is computed, and the alphabet each one draws from.
#:
#: Keyed by (file, exact source text) rather than by line number, on purpose.
#: A line number moves the instant anything is inserted above it, and a stale
#: key would then either fail a clean tree or silently bless a different site
#: that slid into that slot. This is the same reasoning the hygiene gate's
#: allowlist uses, and for the same reason.
#:
#: The value is a dotted path to a `frozenset[str]` living in the module that
#: OWNS the site, so each alphabet has exactly one writer. An unresolved
#: expression that is not registered here fails
#: `test_every_error_site_resolves_to_a_declared_alphabet`.
#: Discovered by running the scan, not by guessing: a first draft of this table
#: was written from a reading of the routers and got five of the eleven wrong.
DECLARED_ALPHABETS: dict[tuple[str, str], str] = {
    ("main.py", "code"):
        "tts.errors:ALL_CODES",
    ("proxy_health.py", 'health.get("reason") or "proxy_unhealthy"'):
        "proxy_health:PROXY_REASONS",
    ("routers/completions.py", "exc.reason"):
        "attachments_service:ATTACHMENT_REASONS",
    # One expression, two channels: the non-streaming raise answers over HTTP
    # and the streaming generator yields the same name into an SSE event.
    ("routers/completions.py", "detail"):
        "routers.completions:RELAY_DETAILS",
    ("routers/completions.py", "code"):
        "routers.completions:CONFLICT_DETAILS",
    ("routers/models_router.py", "reason"):
        "routers.models_router:RELAY_DETAILS",
    # Two different exception families reach this router. NotebookError
    # carries its own finite set. OpenRouterError went through `str(exc)`
    # pointed at completions:RELAY_DETAILS - which was simply WRONG, and
    # invisibly so: RELAY_DETAILS holds the MAPPED details of _ERROR_MAP
    # (`auth_failed`, `api_key_missing`) while `str(exc)` yields the raw
    # REASONS (`openrouter_auth_failed`, `api_key_not_set`). The census
    # therefore credited this router with ten codes it could never send and
    # never saw the five it could, so an expired API key reached this panel as
    # "Something went wrong" while the chat path named it. The router now maps
    # through its own relay, which is what this declaration points at.
    ("routers/notebook.py", "detail"):
        "routers.notebook:RELAY_DETAILS",
    ("routers/notebook.py", "exc.code"):
        "notebook_store:ALL_CODES",
    ("routers/tts_runtime.py", "exc.code"):
        "tts.errors:ALL_CODES",
    ("routers/tts_runtime.py", "_code_for_error(err)"):
        "tts.errors:ALL_CODES",
    ("routers/uploads.py", "exc.reason"):
        "attachments_service:ATTACHMENT_REASONS",
    ("tts/stream_hook.py", "self._pending_error"):
        "tts.errors:ALL_CODES",
    ("tts/stream_hook.py", "_code_for(err)"):
        "tts.errors:ALL_CODES",
}


def _rel(path: Path) -> str:
    return path.relative_to(_BACKEND).as_posix()


def _module_name(rel: str) -> str:
    return rel[:-3].replace("/", ".")


def _resolve(node: ast.AST, rel: str) -> str | None:
    """A literal, or a module-level string constant. Anything else is None.

    The `ast.Name` branch is what makes the thirty-odd TTS routers readable:
    they raise `HTTPException(400, TTS_MODEL_UNKNOWN)` rather than a quoted
    string. Resolved by importing the module rather than by re-parsing the
    definition, which is the pattern `test_tts_contract.py` already trusts for
    the same values.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        try:
            module = importlib.import_module(_module_name(rel))
        except Exception:      # noqa: BLE001 - an unimportable module is not
            return None        # resolvable, and unresolvable is a failure
        value = getattr(module, node.id, None)
        if isinstance(value, str):
            return value
    if isinstance(node, ast.Attribute):
        # `errors.TTS_MODEL_UNKNOWN` and friends.
        try:
            module = importlib.import_module(_module_name(rel))
        except Exception:      # noqa: BLE001
            return None
        owner = getattr(module, node.value.id, None) if isinstance(
            node.value, ast.Name) else None
        value = getattr(owner, node.attr, None) if owner is not None else None
        if isinstance(value, str):
            return value
    return None


def _int_or_none(node: ast.AST | None) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    return None


def _dict_get(node: ast.Dict, key: str) -> ast.AST | None:
    for k, v in zip(node.keys, node.values):
        if isinstance(k, ast.Constant) and k.value == key:
            return v
    return None


def _files() -> list[Path]:
    out = []
    for path in _BACKEND.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in path.relative_to(_BACKEND).parts):
            continue
        out.append(path)
    return sorted(out)


def _called_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def scan() -> tuple[set[Emission], list[Unresolved]]:
    """Every code the backend can construct, and every site we could not read.

    Three constructs, and only three. Anything else that reaches a client with
    a code in it is a shape this module does not know about, which is a reason
    to teach it rather than to widen a pattern until it matches by accident.

    Cached for the process, because the answer cannot change inside one. The
    walk parses every .py file under backend/ and ten callers wanted it during
    a single run: six directly, plus `declared_emissions` and `all_codes`,
    which call it again on their own. Measured at roughly seven seconds of the
    suite spent re-deriving a constant.

    The cached value is FROZEN rather than copied. A copy is a promise the next
    person has to keep; a frozenset and a tuple make poisoning the cache
    impossible rather than merely discouraged. The mutable set and list handed
    back here are fresh each call, so a caller may do what it likes with them.
    """
    emissions, unresolved = _scan_once()
    return set(emissions), list(unresolved)


@lru_cache(maxsize=1)
def _scan_once() -> tuple[frozenset[Emission], tuple[Unresolved, ...]]:
    """The walk itself. `_scan_once.cache_clear()` exists for tests.

    Warmed at the bottom of this module, on purpose. `_resolve` imports the
    production module that owns each site and reads its constants live, so the
    answer depends on what those modules hold AT THE MOMENT OF THE FIRST CALL.
    Without warming, that moment is whichever test happens to ask first, and
    the suite runs in a randomised order: a test holding a monkeypatch over any
    of those constants would have its temporary value frozen into the cache for
    the rest of the session. Importing is the one moment nothing is patched,
    because collection finishes before the first test body runs.
    """
    emissions: set[Emission] = set()
    unresolved: list[Unresolved] = []

    for path in _files():
        rel = _rel(path)
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:                                # pragma: no cover
            continue

        for node in ast.walk(tree):
            # 1. HTTPException(status, detail)
            if isinstance(node, ast.Call) and _called_name(node) == "HTTPException":
                status = _int_or_none(node.args[0] if node.args else None)
                detail = node.args[1] if len(node.args) > 1 else None
                if detail is None:
                    for kw in node.keywords:
                        if kw.arg == "detail":
                            detail = kw.value
                if detail is None:
                    continue
                code = _resolve(detail, rel)
                if code is None:
                    unresolved.append(Unresolved(
                        rel, node.lineno, "http",
                        ast.get_source_segment(source, detail) or "?"))
                else:
                    emissions.add(Emission(code, "http", status, rel, node.lineno))

            # 2. JSONResponse({"detail": code}, status_code=...)
            elif isinstance(node, ast.Call) and _called_name(node) == "JSONResponse":
                body = node.args[0] if node.args else None
                for kw in node.keywords:
                    if kw.arg == "content":
                        body = kw.value
                if not isinstance(body, ast.Dict):
                    continue
                detail = _dict_get(body, "detail")
                if detail is None:
                    continue
                status = None
                for kw in node.keywords:
                    if kw.arg == "status_code":
                        status = _int_or_none(kw.value)
                code = _resolve(detail, rel)
                if code is None:
                    unresolved.append(Unresolved(
                        rel, node.lineno, "http",
                        ast.get_source_segment(source, detail) or "?"))
                else:
                    emissions.add(Emission(code, "http", status, rel, node.lineno))

            # 3. An SSE payload: {"type": <known>, "code": <code>, ...}
            elif isinstance(node, ast.Dict):
                type_node = _dict_get(node, "type")
                if not (isinstance(type_node, ast.Constant)
                        and type_node.value in _SSE_TYPES):
                    continue
                code_node = _dict_get(node, "code")
                if code_node is None:
                    continue
                channel = _SSE_TYPES[type_node.value]
                code = _resolve(code_node, rel)
                if code is None:
                    unresolved.append(Unresolved(
                        rel, node.lineno, channel,
                        ast.get_source_segment(source, code_node) or "?"))
                else:
                    emissions.add(Emission(code, channel, None, rel, node.lineno))

    return emissions, unresolved


def alphabet_for(dotted: str) -> frozenset[str]:
    """Import a declared alphabet. Raises rather than returning empty.

    An alphabet that silently resolves to nothing would make every code drawn
    from it vanish from the census, which is the exact failure this module was
    built to end.
    """
    module_name, _, attr = dotted.partition(":")
    module = importlib.import_module(module_name)
    value = getattr(module, attr)
    if not isinstance(value, frozenset) or not value:
        raise TypeError(f"{dotted} is not a non-empty frozenset[str]")
    return value


def declared_emissions() -> set[Emission]:
    """The codes contributed by every registered dynamic site.

    Every matching site, not the first. `detail` in `routers/completions.py`
    names two of them, one answering over HTTP and one yielded into an SSE
    event, and a first version of this function kept only whichever it saw
    first. The catalogue then recorded that code as reaching the client through
    one exit when it reaches through two, and the channel check would have been
    green about a fact it had the wrong half of.
    """
    out: set[Emission] = set()
    _, unresolved = scan()
    by_key: dict[tuple[str, str], list[Unresolved]] = {}
    for site in unresolved:
        by_key.setdefault((site.file, site.source), []).append(site)
    for (rel, src), dotted in DECLARED_ALPHABETS.items():
        for site in by_key.get((rel, src), ()):
            for code in alphabet_for(dotted):
                out.add(Emission(code, site.channel, None, rel, site.line))
    return out


def all_codes() -> set[str]:
    """Every code the backend can put in front of a user."""
    emissions, _ = scan()
    return ({e.code for e in emissions}
            | {e.code for e in declared_emissions()})


#: Warm the walk here, at import, and not on first use.
#:
#: See `_scan_once`. The snapshot has to be taken at a moment when no test is
#: holding a monkeypatch over a production constant, and collection is the only
#: moment guaranteed to be that: pytest imports every test module before it
#: runs the first test body. Leaving it lazy would make the answer depend on
#: which test asked first, under a randomised order.
_scan_once()
