"""The gate: no logging call in the shipped tree may carry vault content or a
name the app shows on screen.

Companion to `tests/log_leak_scan.py`, which does the actual reading and
explains, in its own module docstring, exactly what shapes it catches and what
it structurally cannot see. Read that file first; this one is the assertions.

WHY THIS IS NOT A BANNED SOURCE SCAN

The house rule bans a test that reads source text AS A SUBSTITUTE for driving
behaviour. This is the exception `tests/test_tree_hygiene.py` already carries
for absence-scanning: there is no behaviour to exercise behind "no exception's
raw message reaches elysium.log" any more than there is behind "no em dash
appears in this repository". The text IS the subject.

THE SCOPE IS NOW THE WHOLE SHIPPED TREE, AND THE PRICE OF THAT IS A LEDGER

It used to be four files, for a stated reason: a gate that started failing on
code its own change never touched would be indistinguishable from a gate that
is simply wrong, and the next person to look would have no way to tell which.
That reason does not disappear because the scope widened, so it is answered
rather than dropped. Every hit that exists TODAY is written down below, per
file, with who owns it and why it is still there. The gate fails on:

  * a hit in a file that is not in the ledger  -> a new leak, fix it;
  * more hits than the ledger records          -> a new leak, fix it;
  * fewer hits than the ledger records         -> somebody FIXED one, and the
    ledger has to say so, because a debt that is quietly retired is a debt
    nobody can prove was ever paid.

The ledger counts hits per file rather than naming line numbers on purpose:
`tts/refs.py` is being rewritten by another agent while this is being
written, and a gate that fails because a paragraph of comment moved a warning
down two lines is a gate people learn to ignore.
"""
from __future__ import annotations

import collections
from pathlib import Path

import pytest

import log_leak_scan
from log_leak_scan import KIND_CONTENT, KIND_TRACEBACK

_BACKEND_ROOT = Path(__file__).resolve().parent.parent

#: Everything under backend/ that ships, which is everything that is not the
#: virtualenv, not bytecode, and not a test. Tests are excluded because they
#: are not in the exe and because fixtures log deliberately noisy things.
_SKIP_PREFIXES = (".venv/", "tests/")


def _swept() -> list[tuple[str, str]]:
    out = []
    for path in sorted(_BACKEND_ROOT.rglob("*.py")):
        rel = path.relative_to(_BACKEND_ROOT).as_posix()
        if rel.startswith(_SKIP_PREFIXES) or "__pycache__" in rel:
            continue
        out.append((rel, path.read_text(encoding="utf-8")))
    return out


#: Floors, not targets (same reasoning as test_tree_hygiene._SWEEP_FLOOR):
#: measured at 175 files / 61688 lines on 2026-08-20. Well under that, so a
#: path that resolved wrong or a sweep that quietly matched nothing fails
#: loudly instead of passing clean.
_FILE_FLOOR = 130
_LINE_FLOOR = 45000

#: Files this pass FIXED. They may never appear in the content ledger again.
_MUST_STAY_CLEAN = (
    "tts/stream_speech.py",
    "tts/worker_client.py",
    "routers/tts_runtime.py",
    # The four the first pass fixed, which is where this gate came from.
    "run_app.py",
    "notebook_worker.py",
    "attachments_service.py",
    "routers/completions.py",
)

#: CONTENT DEBT. A raw value that can carry content or an on-screen name
#: reaching a logging call.
#:
#: tts/refs.py (4): `logger.warning("voice %s ...", voice_id)` in delete() and
#: list_voices(). That file belongs to another agent, who has just finished
#: making the voice FOLDER opaque (sha256 of a per-install key + the id) and
#: has changed the frontend to mint `crypto.randomUUID()` for new voices. For
#: anything created from now on the id is opaque and these lines are fine. For
#: every voice created BEFORE that change the id is still the slug of the
#: label the user typed - refs.py's own docstring says so - and it lives on in
#: that voice's voice.json forever, so the log line still writes an on-screen
#: name outside the vault on any install that is not brand new. Left for the
#: owner of that file, with the legacy-id question escalated rather than
#: decided here.
KNOWN_CONTENT_DEBT: dict[str, int] = {
    "tts/refs.py": 4,
}

#: TRACEBACK DEBT. `logger.exception(...)` or `exc_info=True` inside an except
#: handler: both write the live exception's own message into elysium.log along
#: with the traceback, so both are the content leak above by another route.
#:
#: NOT a fix list. A traceback is the most useful thing in a crash report, and
#: whether this tree should trade all of them away for a leak that is possible
#: rather than demonstrated is a policy question for the owner, not something
#: to settle inside a TTS change. What the ledger buys today is that the
#: number cannot grow without somebody noticing. One was removed on the way
#: past: tts/stream_speech.py's crash handler, because the code inside its try
#: prepares reply text and an exception raised in there arrives holding it.
KNOWN_TRACEBACK_DEBT: dict[str, int] = {
    "auto_lock.py": 1,
    "database.py": 2,
    "generated_images.py": 1,
    "legacy_migration.py": 1,
    "main.py": 2,
    "routers/chats.py": 1,
    "routers/completions.py": 4,
    "routers/tts.py": 3,
    "routers/tts_runtime.py": 2,
    "routers/uploads.py": 1,
    "routers/vault.py": 11,
    "tts/host.py": 3,
    "tts/provision.py": 1,
    "tts/stream_hook.py": 5,
    "tts/worker_client.py": 6,
    "voice_tags.py": 2,
}


@pytest.fixture(scope="module")
def swept() -> list[tuple[str, str]]:
    files = _swept()
    assert len(files) >= _FILE_FLOOR, (
        f"the sweep found only {len(files)} files; floor is {_FILE_FLOOR}. "
        f"Either a path resolved wrong or the tree shrank - re-measure "
        f"before lowering it."
    )
    total = sum(src.count("\n") for _, src in files)
    assert total >= _LINE_FLOOR, (
        f"the sweep read only {total} lines; floor is {_LINE_FLOOR}. An empty "
        f"read and a clean tree produce the same empty hit list."
    )
    return files


@pytest.fixture(scope="module")
def hits(swept: list[tuple[str, str]]) -> list[log_leak_scan.Hit]:
    out: list[log_leak_scan.Hit] = []
    for rel, src in swept:
        out.extend(log_leak_scan.scan_source(src, path=rel))
    return out


def _counts(hits: list[log_leak_scan.Hit], kind: str) -> dict[str, int]:
    counter: collections.Counter = collections.Counter(
        h.path for h in hits if h.kind == kind)
    return dict(counter)


def _compare(found: dict[str, int], ledger: dict[str, int], what: str,
             advice: str) -> None:
    lines: list[str] = []
    for path, n in sorted(found.items()):
        known = ledger.get(path, 0)
        if n > known:
            lines.append(f"  {path}: {n} {what} hit(s), ledger allows {known}")
    for path, known in sorted(ledger.items()):
        n = found.get(path, 0)
        if n < known:
            lines.append(
                f"  {path}: {n} {what} hit(s), ledger still claims {known} - "
                f"somebody fixed one. Lower the number (or delete the entry)."
            )
    if not lines:
        return
    raise AssertionError("\n".join(["", f"{what} ledger no longer matches:", ""]
                                   + lines + ["", advice]))


class TestTheScannerCanActuallyFire:
    """The positive control. A gate that has never been seen to fail is a
    gate nobody has tested, only trusted. One case per shape the scanner
    claims to catch, and one per exemption it claims to grant."""

    def test_a_raw_exception_is_caught(self) -> None:
        source = (
            "import logging\n"
            "logger = logging.getLogger(__name__)\n"
            "def f():\n"
            "    try:\n"
            "        risky()\n"
            "    except ValueError as exc:\n"
            "        logger.warning('bad: %s', exc)\n"
        )
        hits = log_leak_scan.scan_source(source, path="<control>")
        assert len(hits) == 1
        assert hits[0].lineno == 7

    def test_a_raw_exception_behind_or_is_still_caught(self) -> None:
        # The exact shape tts/stream_speech.py used to use: `exc.__cause__ or
        # exc` still puts the bare name `exc` in the expression tree, and the
        # scanner must not stop at the first (safe-looking) attribute access.
        source = (
            "import logging\n"
            "logger = logging.getLogger(__name__)\n"
            "def f():\n"
            "    try:\n"
            "        risky()\n"
            "    except Exception as exc:\n"
            "        logger.warning('bad: %s', exc.__cause__ or exc)\n"
        )
        hits = log_leak_scan.scan_source(source, path="<control>")
        assert len(hits) >= 1

    def test_an_intermediate_variable_is_caught(self) -> None:
        # Blind spot the first version of this scanner named in its own
        # docstring, now closed: the leak is the same leak one line apart.
        source = (
            "import logging\n"
            "logger = logging.getLogger(__name__)\n"
            "def f():\n"
            "    try:\n"
            "        risky()\n"
            "    except Exception as exc:\n"
            "        msg = str(exc)\n"
            "        logger.warning('bad: %s', msg)\n"
        )
        hits = log_leak_scan.scan_source(source, path="<control>")
        assert len(hits) == 1
        assert hits[0].lineno == 8

    def test_an_intermediate_f_string_is_caught(self) -> None:
        source = (
            "import logging\n"
            "logger = logging.getLogger(__name__)\n"
            "def f():\n"
            "    try:\n"
            "        risky()\n"
            "    except Exception as exc:\n"
            "        msg = f'while speaking: {exc}'\n"
            "        logger.warning(msg)\n"
        )
        assert len(log_leak_scan.scan_source(source, path="<control>")) == 1

    def test_a_rebinding_to_something_clean_clears_the_taint(self) -> None:
        source = (
            "import logging\n"
            "logger = logging.getLogger(__name__)\n"
            "def f():\n"
            "    try:\n"
            "        risky()\n"
            "    except Exception as exc:\n"
            "        msg = str(exc)\n"
            "        msg = 'synthesis failed'\n"
            "        logger.warning(msg)\n"
        )
        assert log_leak_scan.scan_source(source, path="<control>") == []

    def test_a_helper_that_logs_its_parameter_is_caught(self) -> None:
        # routers/tts_runtime.py, exactly: `_fail(exc)` several hundred lines
        # from the handler that binds `exc`. The first version of this
        # scanner could not see it, which is why leak number two survived the
        # pass that built the scanner.
        source = (
            "import logging\n"
            "logger = logging.getLogger(__name__)\n"
            "def _fail(err):\n"
            "    logger.warning('tts: %s', err.detail)\n"
            "def f():\n"
            "    try:\n"
            "        risky()\n"
            "    except Exception as exc:\n"
            "        _fail(exc)\n"
        )
        hits = log_leak_scan.scan_source(source, path="<control>")
        # ONE hit, at the CALL, not inside the helper. A parameter is not
        # tainted on its own - plenty of callers pass `_fail` something
        # harmless - so the place that needs to change is the line that hands
        # it a live exception.
        assert [h.lineno for h in hits] == [9]
        assert "_fail()" in hits[0].what

    def test_a_helper_that_logs_a_different_parameter_is_not_a_dragnet(self) -> None:
        # Precision matters as much as reach here: a helper that logs one of
        # its arguments is not thereby a hazard for the other five, and
        # treating it as one flagged a completions.py call for a log line
        # about something else entirely.
        source = (
            "import logging\n"
            "logger = logging.getLogger(__name__)\n"
            "def helper(kind, err):\n"
            "    logger.warning('%s', err.detail)\n"
            "def f():\n"
            "    try:\n"
            "        risky()\n"
            "    except Exception as exc:\n"
            "        helper(exc, None)\n"
            "        helper('load', exc)\n"
        )
        hits = log_leak_scan.scan_source(source, path="<control>")
        # Line 10 hands the exception to the parameter that gets logged.
        # Line 9 hands it to `kind`, which never reaches a logger, so it is
        # left alone.
        assert [h.lineno for h in hits] == [10]

    def test_a_denylisted_name_is_caught_outside_any_except_handler(self) -> None:
        source = (
            "import logging\n"
            "logger = logging.getLogger(__name__)\n"
            "def f(description):\n"
            "    logger.info('card: %s', description)\n"
        )
        hits = log_leak_scan.scan_source(source, path="<control>")
        assert len(hits) == 1
        assert "description" in hits[0].what

    def test_a_denylisted_name_formatted_into_a_string_is_caught(self) -> None:
        source = (
            "import logging\n"
            "logger = logging.getLogger(__name__)\n"
            "def f(text):\n"
            "    line = 'spoke: ' + text\n"
            "    logger.info(line)\n"
        )
        assert len(log_leak_scan.scan_source(source, path="<control>")) == 1

    def test_a_row_id_from_a_statement_that_mentions_content_is_not_flagged(
            self) -> None:
        # The measured false positive that made content taint spread only
        # through string building. A row id is precisely what the owner said
        # belongs in the log, and the gate flagging it would have taught
        # everyone to route around the gate.
        source = (
            "import logging\n"
            "logger = logging.getLogger(__name__)\n"
            "def f(con, chat_id, text):\n"
            "    cur = con.execute('INSERT ...', (chat_id, text))\n"
            "    msg_id = cur.lastrowid\n"
            "    logger.info('stored id=%d', msg_id)\n"
        )
        assert log_leak_scan.scan_source(source, path="<control>") == []

    def test_a_logger_passed_in_as_log_is_seen(self) -> None:
        source = (
            "def f(log):\n"
            "    try:\n"
            "        risky()\n"
            "    except Exception as exc:\n"
            "        log.warning('bad: %s', exc)\n"
        )
        assert len(log_leak_scan.scan_source(source, path="<control>")) == 1

    def test_logger_exception_in_a_handler_is_caught_as_a_traceback(self) -> None:
        source = (
            "import logging\n"
            "logger = logging.getLogger(__name__)\n"
            "def f():\n"
            "    try:\n"
            "        risky()\n"
            "    except Exception:\n"
            "        logger.exception('crashed')\n"
        )
        hits = log_leak_scan.scan_source(source, path="<control>")
        assert [h.kind for h in hits] == [KIND_TRACEBACK]

    def test_exc_info_true_in_a_handler_is_caught_as_a_traceback(self) -> None:
        source = (
            "import logging\n"
            "logger = logging.getLogger(__name__)\n"
            "def f():\n"
            "    try:\n"
            "        risky()\n"
            "    except Exception:\n"
            "        logger.debug('ended', exc_info=True)\n"
        )
        hits = log_leak_scan.scan_source(source, path="<control>")
        assert [h.kind for h in hits] == [KIND_TRACEBACK]

    def test_exc_info_false_is_not_a_traceback(self) -> None:
        source = (
            "import logging\n"
            "logger = logging.getLogger(__name__)\n"
            "def f():\n"
            "    try:\n"
            "        risky()\n"
            "    except Exception:\n"
            "        logger.debug('ended', exc_info=False)\n"
        )
        assert log_leak_scan.scan_source(source, path="<control>") == []

    def test_logger_exception_outside_a_handler_is_not_a_traceback(self) -> None:
        # Nothing live to serialise, so nothing to leak.
        source = (
            "import logging\n"
            "logger = logging.getLogger(__name__)\n"
            "def f():\n"
            "    logger.exception('odd but harmless')\n"
        )
        assert log_leak_scan.scan_source(source, path="<control>") == []

    def test_type_name_is_the_approved_escape_hatch(self) -> None:
        source = (
            "import logging\n"
            "logger = logging.getLogger(__name__)\n"
            "def f():\n"
            "    try:\n"
            "        risky()\n"
            "    except ValueError as exc:\n"
            "        logger.warning('bad: %s', type(exc).__name__)\n"
        )
        assert log_leak_scan.scan_source(source, path="<control>") == []

    def test_the_class_itself_is_safe_too(self) -> None:
        # `_CONFLICT_CODES[type(exc)]` in completions.py: a lookup into a
        # fixed vocabulary. A class object carries no instance message.
        source = (
            "import logging\n"
            "logger = logging.getLogger(__name__)\n"
            "CODES = {}\n"
            "def f():\n"
            "    try:\n"
            "        risky()\n"
            "    except ValueError as exc:\n"
            "        code = CODES[type(exc)]\n"
            "        logger.warning('conflict: %s', code)\n"
        )
        assert log_leak_scan.scan_source(source, path="<control>") == []

    def test_a_length_is_a_number_not_the_thing_measured(self) -> None:
        source = (
            "import logging\n"
            "logger = logging.getLogger(__name__)\n"
            "def f(text):\n"
            "    logger.info('chars=%d', len(text))\n"
        )
        assert log_leak_scan.scan_source(source, path="<control>") == []

    def test_sanitized_reason_is_the_other_approved_escape_hatch(self) -> None:
        source = (
            "import logging\n"
            "logger = logging.getLogger(__name__)\n"
            "def f():\n"
            "    try:\n"
            "        risky()\n"
            "    except AttachmentError as exc:\n"
            "        logger.warning('bad: %s', exc.reason)\n"
        )
        assert log_leak_scan.scan_source(source, path="<control>") == []

    def test_a_literal_getattr_for_a_safe_attribute_is_allowed(self) -> None:
        source = (
            "import logging\n"
            "logger = logging.getLogger(__name__)\n"
            "def f():\n"
            "    try:\n"
            "        risky()\n"
            "    except Exception as exc:\n"
            "        logger.warning('bad: %s', getattr(exc, 'code', ''))\n"
        )
        assert log_leak_scan.scan_source(source, path="<control>") == []

    def test_a_computed_getattr_is_refused_rather_than_guessed_at(self) -> None:
        source = (
            "import logging\n"
            "logger = logging.getLogger(__name__)\n"
            "def f(which):\n"
            "    try:\n"
            "        risky()\n"
            "    except Exception as exc:\n"
            "        logger.warning('bad: %s', getattr(exc, which, ''))\n"
        )
        assert len(log_leak_scan.scan_source(source, path="<control>")) == 1

    def test_a_getattr_for_an_unsafe_attribute_is_caught(self) -> None:
        source = (
            "import logging\n"
            "logger = logging.getLogger(__name__)\n"
            "def f():\n"
            "    try:\n"
            "        risky()\n"
            "    except Exception as exc:\n"
            "        logger.warning('bad: %s', getattr(exc, 'args', ''))\n"
        )
        assert len(log_leak_scan.scan_source(source, path="<control>")) == 1

    def test_a_plain_id_is_not_flagged(self) -> None:
        # The rule this whole change protects: numeric ids stay in the log.
        source = (
            "import logging\n"
            "logger = logging.getLogger(__name__)\n"
            "def f(chat_id):\n"
            "    logger.info('chat_id=%d', chat_id)\n"
        )
        assert log_leak_scan.scan_source(source, path="<control>") == []


def test_the_files_this_pass_fixed_carry_no_content_leak(
        hits: list[log_leak_scan.Hit]) -> None:
    """Named separately from the ledger so that adding one of them TO the
    ledger cannot be how a regression gets accepted."""
    found = _counts(hits, KIND_CONTENT)
    guilty = {p: n for p, n in found.items() if p in _MUST_STAY_CLEAN}
    assert not guilty, (
        f"these files were fixed and must stay clean: {guilty}. "
        f"Run the scanner for the detail; do not add them to the ledger."
    )


def test_no_new_content_leak_anywhere_in_the_tree(
        hits: list[log_leak_scan.Hit]) -> None:
    _compare(
        _counts(hits, KIND_CONTENT), KNOWN_CONTENT_DEBT, "content",
        "elysium.log is plaintext, outside the vault, and survives a lock. A "
        "numeric id is fine there; content and on-screen names are not.\n"
        "Fix: log type(exc).__name__ or exc.reason/exc.code instead of the "
        "exception itself, or stop passing the denylisted value at all.",
    )


def test_no_new_traceback_leak_anywhere_in_the_tree(
        hits: list[log_leak_scan.Hit]) -> None:
    _compare(
        _counts(hits, KIND_TRACEBACK), KNOWN_TRACEBACK_DEBT, "traceback",
        "logger.exception(...) and exc_info=True write the live exception's "
        "own message into elysium.log along with the traceback. If the code "
        "inside that try can touch a message, a note, a card or anything a "
        "person typed, log type(exc).__name__ instead and say so in a "
        "comment. If it genuinely cannot, add it to KNOWN_TRACEBACK_DEBT "
        "with the reason.",
    )
