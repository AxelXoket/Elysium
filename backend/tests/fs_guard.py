"""The other promise nothing enforced: a test run does not touch real data.

egress_guard.py says no test may leave this machine. This says no test may
write to the data this machine already holds - the vault the developer opens
Elysium with, and the packaged app's own directory under %LOCALAPPDATA%.

WHY IT IS NEEDED, AND IT IS NOT A HYPOTHETICAL

The defect record for this began as a near-miss: adding one vault HTTP call
inside a particular try block would have overwritten the real salt.bin and
verifier.bin. But the measurement that preceded this file found something
already happening, on every run, in five files.

config.TTS_CACHE_DIR defaults to backend/voice/cache. Nothing redirected it.
routers/vault.py::_purge_voice_cache is the first statement of the unlock
bootstrap, and it does not delete - it OVERWRITES with random bytes and then
unlinks. So test_vault.py alone, with its twenty-eight init and unlock posts,
destroyed the developer's own spoken replies every time the suite ran. It read
as "blast radius zero" only because that folder happened to be empty on the
machine where it was measured.

WHAT IT REFUSES

Named paths, not a rule about where a test may write. An allow-list would have
to tolerate __pycache__, .pytest_cache, .ruff_cache and the system temp
directory, none of which are the tests' doing, and the exceptions would soon
outnumber the rule. So this is a deny-list, and the list is derived from
config rather than typed out: every path the application treats as the user's
data, plus the packaged location.

THE HONEST LIMIT, and it is a real one

monkeypatch rewrites attributes in ONE interpreter. A subprocess gets a clean
socket module and a clean filesystem, and this suite spawns around twenty of
them - fake TTS workers, a nested pytest, uv, and the packaged exe itself. Two
of those already point a fresh interpreter at a real data directory. Nothing
here can see any of it, and the answer for those is the same as egress's: hand
the child an isolated ELYSIUM_DATA_DIR and assert on what it was handed.
"""
from __future__ import annotations

import builtins
import io
import os
import shutil
import sysconfig
import tempfile
from pathlib import Path


class ForbiddenWrite(AssertionError):
    """Raised when the suite tried to write to the machine's real data."""


def _real_data_paths() -> list[Path]:
    """Everything the application calls the user's data, asked of config.

    Read from config rather than listed here, so a new directory added there
    is covered without anybody remembering to come back. The one hardcoded
    entry is the packaged location, which config only computes in a frozen
    build and therefore never reports during a test run.
    """
    import config

    guarded = [
        Path(config.DB_PATH),
        Path(config.DATA_DIR) / "salt.bin",
        Path(config.DATA_DIR) / "verifier.bin",
        Path(config.DATA_DIR) / "kdf.json",
        Path(config.DATA_DIR) / "vault.recovery",
        Path(config.UPLOADS_DIR),
        Path(config.TTS_DIR),
    ]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        guarded.append(Path(local) / "Elysium")
    return [p for p in guarded]


def _temp_root() -> Path | None:
    """The system temp directory, which this guard treats as a THIRD place.

    Not user data, and not covered by the deny-list above - which is exactly
    why it was a blind spot. The upload path spools a body here once it
    outgrows its buffer, and the voice route's clip goes with it, in the
    clear. A test could not see that, because nothing here looked at temp.

    It is not a plain deny-list entry, because pytest's own `tmp_path` lives
    under temp and every fixture in this suite would refuse itself. What is
    refused is a write into the temp ROOT; a write inside pytest's basetemp,
    or inside any directory a test made for itself, is allowed.
    """
    try:
        return Path(tempfile.gettempdir()).resolve()
    except (OSError, ValueError):  # pragma: no cover - defensive
        return None


def _resolve(target: object) -> Path | None:
    try:
        return Path(os.fspath(target)).resolve()
    except (TypeError, ValueError, OSError):
        return None  # an fd, a socket, something that is not a path at all


def _refuse(where: Path, guarded: Path, how: str) -> ForbiddenWrite:
    return ForbiddenWrite(
        f"a test tried to {how} {where}, which is inside {guarded} - the real "
        f"data on this machine, not a fixture. Point the setting at tmp_path "
        f"instead. If a test genuinely needs to prove the app writes there, "
        f"assert on ForbiddenWrite rather than allowing it."
    )


_TESTS_DIR = Path(__file__).resolve().parent
#: Where the standard library lives, so its frames can be stepped over. Read
#: from sysconfig rather than guessed from sys.prefix: a virtualenv reports a
#: prefix of its own and the stdlib is not under it.
_STDLIB = sysconfig.get_paths()["stdlib"]


def _called_from_tests() -> bool:
    """Is the caller a test, rather than the application under test?

    Walked lazily and only on a temp-root hit, because this is not cheap and
    the filesystem wrappers run on every call in the suite. `sys._getframe`
    rather than `inspect.stack`: the latter reads source lines for every
    frame, which turns a rare check into a visible cost.
    """
    import sys

    depth = 2
    while True:
        try:
            frame = sys._getframe(depth)
        except ValueError:
            return False
        name = frame.f_code.co_filename
        # This module's own wrappers are plumbing, and so is the standard
        # library: `TemporaryDirectory` calls `mkdtemp` calls `os.mkdir`, so
        # the first frame outside here is `tempfile.py` and judging it would
        # answer "not a test" for every caller alive. Skip both and let the
        # first frame somebody in this repository wrote decide.
        if name != __file__ and not name.startswith(_STDLIB):
            try:
                return _TESTS_DIR in Path(name).resolve().parents
            except (OSError, ValueError):  # pragma: no cover - defensive
                return False
        depth += 1


def install(monkeypatch) -> None:
    """Refuse writes to the machine's own data for the duration of one test."""
    guarded = [p for p in (_resolve(q) for q in _real_data_paths()) if p]
    temp_root = _temp_root()

    def check(target: object, how: str) -> None:
        where = _resolve(target)
        if where is None:
            return
        for root in guarded:
            if where == root or root in where.parents:
                raise _refuse(where, root, how)
        # The temp root, and only its immediate children, and only when the
        # write comes from the APPLICATION.
        #
        # Two narrowings, both learned by measuring. A path deeper than the
        # root belongs to a directory somebody made on purpose - pytest's
        # basetemp, a fixture's scratch dir - so only the root's own children
        # count. And a test building itself a scratch directory is not the
        # thing this catches: `test_image_verify_unlock.py` opens
        # `TemporaryDirectory(prefix="elysium-imgverify-")` on purpose, and
        # refusing it would be the guard failing the suite for doing the
        # right thing. What is left is the case that matters: application
        # code dropping a file straight into %TEMP%, which is how user
        # content leaves the vault without any line saying the word "temp".
        if temp_root is not None and where.parent == temp_root:
            if not _called_from_tests():
                raise _refuse(where, temp_root, how)

    def wrap_one(module, name: str, index: int, how: str) -> None:
        original = getattr(module, name, None)
        if original is None:  # pragma: no cover - defensive
            return

        def guarded_call(*args, **kwargs):
            if len(args) > index:
                check(args[index], how)
            return original(*args, **kwargs)

        monkeypatch.setattr(module, name, guarded_call)

    def wrap_open(module, name: str) -> None:
        original = getattr(module, name)

        def guarded_open(file, mode="r", *args, **kwargs):
            # Reading is fine and constant: the suite imports its own source,
            # reads fixtures, and hashes the exe. Only a mode that can change
            # bytes is refused.
            if any(ch in str(mode) for ch in ("w", "a", "x", "+")):
                check(file, "open for writing")
            return original(file, mode, *args, **kwargs)

        monkeypatch.setattr(module, name, guarded_open)

    wrap_open(builtins, "open")
    # Not the same function object. Path.write_bytes and friends go through
    # io.open, so patching builtins alone leaves every one of them uncovered.
    wrap_open(io, "open")

    for name in ("write_text", "write_bytes", "mkdir", "unlink", "touch",
                 "rmdir"):
        wrap_one(Path, name, 0, "write to")
    # Both ends of a rename: the source is destroyed and the target is
    # replaced, so either one landing inside real data is a write.
    for name in ("replace", "rename"):
        wrap_one(Path, name, 0, "move")
        wrap_one(Path, name, 1, "write to")
    for name in ("unlink", "remove", "rmdir", "makedirs", "mkdir", "truncate"):
        wrap_one(os, name, 0, "write to")
    for name in ("replace", "rename"):
        wrap_one(os, name, 0, "move")
        wrap_one(os, name, 1, "write to")
    for name in ("rmtree", "copy", "copy2", "copyfile", "move"):
        wrap_one(shutil, name, 0, "write to")
    for name in ("copy", "copy2", "copyfile", "move"):
        wrap_one(shutil, name, 1, "write to")
    # runtimes.json is written through a raw fd, so it reaches the filesystem
    # past both open() and every Path method. The directory is what is
    # checkable here; the fd afterwards is not.
    #
    # It was wrapped at argument 0 until 22 August 2026, and that checked
    # NOTHING: mkstemp's signature is (suffix, prefix, dir, text), so index 0
    # is the suffix - a string like ".tmp" that resolves to a relative path
    # and never matches a guarded root. The wrapper ran on every call and
    # refused nothing. The directory is argument 2, and callers usually pass
    # it by keyword, which positional indexing cannot see at all.
    #
    # mkdtemp has the same signature and was not wrapped at all.
    # `dir_index` is None where the positional slot is not the directory:
    # NamedTemporaryFile and TemporaryFile take (mode, buffering, encoding,
    # newline, suffix, prefix, dir, ...), so counting positions there would
    # check the encoding. Those two are guarded on the keyword only, which is
    # the only way this tree calls them.
    def wrap_temp_factory(name: str, dir_index: int | None) -> None:
        original = getattr(tempfile, name)

        def guarded_temp(*args, **kwargs):
            if "dir" in kwargs:
                check(kwargs["dir"], "write to")
            elif dir_index is not None and len(args) > dir_index:
                check(args[dir_index], "write to")
            return original(*args, **kwargs)

        monkeypatch.setattr(tempfile, name, guarded_temp)

    for name, dir_index in (("mkstemp", 2), ("mkdtemp", 2),
                            ("NamedTemporaryFile", None),
                            ("TemporaryFile", None)):
        wrap_temp_factory(name, dir_index)

    # The vault is not stdlib sqlite3 - that import exists in one module for a
    # type annotation only. connect() creates the file, so it is a write.
    try:
        from sqlcipher3 import dbapi2
    except Exception:  # pragma: no cover - the vault driver is a hard dep
        return
    real_connect = dbapi2.connect

    def guarded_connect(database, *args, **kwargs):
        check(database, "open a database at")
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(dbapi2, "connect", guarded_connect)
