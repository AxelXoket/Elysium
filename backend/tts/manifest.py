"""tts/manifest.py - what the download actually wrote, so a short file is caught.

A half-finished download looks exactly like a working model. The name is there,
`is_file()` says yes, `readiness.py` finds nothing to report, and the first
thing that notices the truncation is torch, at load time, as a stack trace.
That is the precise failure `readiness.py:197-198` says it exists to prevent,
and it walked straight through the check (defect Q-28).

The number nobody had was the size the file was SUPPOSED to be. `signature_files`
reports the size a file HAS, which cannot answer the question. So the source of
truth is recorded at the only moment anything knows it: the downloader writes
this manifest beside the model once the fetch is complete, naming each file it
wrote and how many bytes it wrote.

SIZE, NOT A HASH. The failure in front of us is the interrupted download, and
an interrupted download is short. A hash would also catch bit rot, at the cost
of reading gigabytes on every settings page render, for a fault nobody has
reported. Size is one stat per file, which is what a page listing twenty models
can afford.

NO MANIFEST MEANS NO CLAIM. Every model already sitting on a user's disk was
put there before this file existed, so it has no manifest and never will. Those
must keep working exactly as they do today: absence is silence, never a verdict.
This is a strengthening for downloads made from now on, not a new way to refuse
an install that has been speaking for months.

UNTRUSTED INPUT. The manifest lives in a folder the user dropped in, so every
read here degrades to "no manifest" rather than raising, and every path in it
is confined to the model folder before anything is stat-ed. A readiness verdict
must not fail, or leak, because somebody put junk in a JSON file.
"""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path

from .util import read_json

#: Beside the weights, like the engine sidecar, and for the same reason: the
#: folder should still describe itself after a move or a reinstall.
MANIFEST_NAME = "elysium-download.json"

#: Bumped only when the SHAPE changes. An unknown version reads as no manifest,
#: so an older build meeting a newer file makes no claim instead of a wrong one.
MANIFEST_VERSION = 1

#: A real model folder is a few dozen files; the widest realistic layout, a
#: sharded HF snapshot, is well under a hundred. Past this the file is not
#: something any downloader of ours produced, and checking an unbounded list
#: would put a number from a user-supplied file in charge of how long the
#: settings page takes to render. MEASURED on this machine: about 0.2 ms per
#: entry, so 256 costs 52 ms for one model and roughly a second for a page
#: listing twenty. 4096, the first number written here, cost 815 ms EACH and
#: would have turned that page into a sixteen-second stall.
#:
#: Erring low is the safe direction and that is why the number can be tight:
#: over the cap we make NO claim, which is exactly what a model with no
#: manifest gets. A cap set too low loses a check; it never invents a refusal.
MAX_ENTRIES = 256

#: And the cost of ONE entry, which capping the count does not touch. This was
#: the hole under the paragraph above: `_inside` calls `resolve()`, and
#: `resolve()` is priced by how many components the path has. MEASURED: a key
#: 8000 components deep costs about 2 SECONDS on its own, so twenty of them -
#: a 320 KB file, one twelfth of what the reader accepts, and one twelfth of
#: the entry cap - stalled a single model for 28 seconds. The count cap bought
#: nothing there because the wrong quantity was capped.
#:
#: 260 is the classic Windows path limit and 16 is four times the deepest real
#: layout (an HF snapshot nests three or four). Nothing legitimate comes near
#: either, and over them we make no claim, same as everywhere else here.
MAX_KEY_CHARS = 260
MAX_KEY_PARTS = 16

#: Windows resolves these names to devices wherever they appear, so a key
#: naming one is not naming a file in this folder. `<model>/NUL` stats happily
#: as zero bytes and would be reported as a short download of a file the user
#: cannot see. Compared case-insensitively and without the extension, which is
#: how Windows matches them.
_DEVICE_NAMES = frozenset(
    ["con", "prn", "aux", "nul"]
    + [f"com{i}" for i in range(1, 10)]
    + [f"lpt{i}" for i in range(1, 10)]
)


def _inside(model_dir: Path, base: Path, rel: str) -> Path | None:
    """The file `rel` names, or None if it does not name one INSIDE this folder.

    `base` is the caller's already-resolved `model_dir`, passed in rather than
    recomputed: this runs once per entry, and a manifest at the cap turns one
    avoidable syscall into thousands on a page that lists twenty models.

    The keys of this manifest are untrusted text. `..\\..\\Windows\\win.ini` is
    a perfectly valid JSON key, and stat-ing whatever it points at would turn
    the settings page into a file-existence oracle for the rest of the disk.
    So what is ACCEPTED is narrow on purpose - a plain relative path of plain
    names - rather than a list of the tricks anyone has thought of yet.

    The refusals, in this order, and the order is the point:

      * Anything with a root, a drive or a UNC share. This one comes FIRST
        because the confinement below has to call `resolve()`, and resolving
        `\\\\some-host\\share\\x` reaches for the network. A settings page must
        not block on a machine somebody named inside a JSON file, so that name
        never reaches a syscall. `C:file.txt` is caught here too: it looks
        relative and is drive-relative, which is a different folder entirely.
      * A NUL anywhere. `Path.stat` raises ValueError rather than OSError on an
        embedded NUL, and that ValueError used to escape the per-entry loop
        into the blanket handler in `short_files` - one junk key switched the
        check off for every other file in the folder, which is fail-open in
        exactly the case this module exists for.
      * A colon anywhere. On NTFS `model.pth:evil` names an alternate data
        STREAM of a file, not a file, so it reports a name the user cannot see
        in their folder. No relative path we write contains one.
      * A name part that is empty, or ends in a space or a dot. Windows strips
        both at the filesystem boundary, so `model.pth ` and `model.pth.`
        quietly stat `model.pth` and the verdict then names a file that is not
        in the folder. `.` and `  ` are the same trick aimed at the folder
        itself.
      * Anything containing `..`, even when it lands back inside the folder.
        `extra/../model.pth` and `model.pth` are the same file spelled two
        ways, and a manifest that can say one thing twice is one we cannot
        reason about. Nothing we write produces it.
      * Whatever is left is resolved and required to sit STRICTLY under the
        model folder, which is what catches a junction or symlink planted
        inside it. Strictly: the folder itself is not a file we can record, so
        accepting it only ever produced a verdict about a directory.
    """
    try:
        if len(rel) > MAX_KEY_CHARS or "\x00" in rel or ":" in rel:
            return None
        raw = Path(rel)
        if len(raw.parts) > MAX_KEY_PARTS:
            return None
        if raw.is_absolute() or raw.drive or raw.anchor:
            return None
        parts = raw.parts
        if not parts:
            return None
        for part in parts:
            if part == ".." or not part.strip() or part[-1] in " .":
                return None
            if part.split(".")[0].casefold() in _DEVICE_NAMES:
                return None
        target = model_dir / raw
        if base not in target.resolve().parents:
            return None
        return target
    except (OSError, ValueError):
        return None


#: Not a size any file can have, so it can never accidentally equal a recorded
#: one. Zero was the obvious choice and it was wrong: a downloader can record a
#: legitimately empty file as 0, and then deleting that file left 0 == 0 and
#: the loss went unreported.
ABSENT = -1


def _size_on_disk(path: Path) -> int | None:
    """Bytes; ABSENT for a name that is not there; None when we cannot tell.

    `util.size_of` answers 0 to all three, and that conflation would be a
    serious thing to get wrong HERE rather than in an adapter. The verdict this
    feeds says "your model is incomplete, download it again", and on a large
    engine that is several gigabytes of somebody's connection. A file we were
    merely refused permission to look at, or one on a drive that blinked, must
    not buy that sentence: we cannot see it, so we say nothing about it.

    A file that is genuinely GONE is a different answer and is reported. That
    one is not a guess - the directory entry is not there - and it is the same
    fault as a short file wearing a different hat.

    ValueError is caught beside OSError deliberately. `Path.stat` raises it,
    not an OSError, for a path with an embedded NUL, and letting that escape
    turned one junk key into an amnesty for every other file in the folder.
    """
    try:
        return path.stat().st_size
    except (FileNotFoundError, NotADirectoryError):
        # NotADirectoryError is the same news through another door: a component
        # of the path is a file, so the name cannot exist.
        return ABSENT
    except (OSError, ValueError):
        return None


def read(model_dir: Path) -> dict[str, int] | None:
    """Relative path -> the byte size the download wrote. None if no claim.

    None and `{}` are different answers and the difference is worth keeping:
    None is "nobody ever recorded anything about this folder", `{}` is "a
    manifest is here and every entry in it was unusable". Both mean the caller
    says nothing, but only the second one means somebody wrote a bad file.

    Never raises.
    """
    entries = _entries(model_dir)
    if entries is None:
        return None
    return {rel: size for rel, _target, size in entries}


def _entries(model_dir: Path) -> list[tuple[str, Path, int]] | None:
    """Every usable entry as (relpath, the file it names, expected size).

    One pass, so the confinement is applied exactly once per entry and both
    public functions above work from the same answer. Splitting it out is not
    tidiness: `read` and `short_files` each ran it before, which quadrupled the
    syscalls behind a settings page that already lists twenty models.

    Never raises.
    """
    try:
        model_dir = Path(model_dir)
        data = read_json(model_dir / MANIFEST_NAME)
        if not data:
            return None
        # Typed the same way the sizes below are, and for the same reason:
        # `True == 1` and `1.0 == 1` in Python, so a plain `!=` accepted a
        # boolean and a float as version 1. The neighbour three lines down
        # already guards that trap; this one was not.
        version = data.get("version")
        if isinstance(version, bool) or not isinstance(version, int) \
                or version != MANIFEST_VERSION:
            return None
        files = data.get("files")
        if not isinstance(files, dict) or len(files) > MAX_ENTRIES:
            return None
        base = model_dir.resolve()
        out: list[tuple[str, Path, int]] = []
        seen: set[str] = set()
        for rel, size in files.items():
            if not isinstance(rel, str) or not rel:
                continue
            # bool is an int in Python and `True` is not a byte count. An entry
            # we cannot read is skipped rather than failing the whole manifest:
            # one bad line must not switch off the check for the other forty.
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                continue
            # normcase, not ==, because NTFS does not care about case and this
            # comparison must not either: `Elysium-Download.json` slipped past
            # the plain check and had the manifest measuring itself.
            if os.path.normcase(rel) == os.path.normcase(MANIFEST_NAME):
                continue      # it cannot record its own size; ignore the try
            target = _inside(model_dir, base, rel)
            if target is None:
                continue
            # TWO SPELLINGS OF ONE FILE MEAN NO CLAIM AT ALL. Windows resolves
            # `model.pth` and `MODEL.PTH` to the same bytes, so a manifest
            # carrying both is claiming that one file has two sizes. There is
            # no way to pick a winner, and picking one produced the worst
            # outcome available: "MODEL.PTH is not the size the download
            # recorded" about a file that is correct and is not even spelled
            # that way on disk. The whole manifest is incoherent, so it is
            # refused whole - which lands on "no manifest, no claim", the
            # direction this module always fails in. `write` refuses to
            # produce one, so ours never take this path.
            # Normalised, not raw: `./model.pth` and `model.pth` are one file
            # and the raw strings do not collide. `Path` has already folded
            # away the `./` and the separator style by this point.
            key = os.path.normcase(str(Path(rel)))
            if key in seen:
                return None
            seen.add(key)
            out.append((rel, target, size))
        return out
    except Exception:                                    # noqa: BLE001
        return None


def short_files(model_dir: Path) -> tuple[str, ...]:
    """Files whose size on disk is not the size the download recorded.

    "Short" is the shape of the fault that motivated this, but the test is
    DIFFERS, not smaller: a file that grew is just as much a sign that these
    bytes are not the bytes that were fetched.

    A file that vanished outright comes back here too, at ANY recorded size
    including zero. Same fault, different hat, and the caller says it the same
    way. That is what `ABSENT` is for: reading a gone file as 0 bytes let a
    legitimately empty file be deleted without a word.

    A file we could not measure at all is NOT reported. See `_size_on_disk`:
    "I was not allowed to look" is not evidence of a bad download, and the
    sentence this produces costs the user a multi-gigabyte re-download.

    Empty when there is no manifest, which is the whole no-claim rule. Never
    raises: a readiness verdict must not fail because a model folder is odd.
    """
    try:
        entries = _entries(model_dir)
        if not entries:
            return ()
        bad = []
        for rel, target, size in entries:
            actual = _size_on_disk(target)
            if actual is not None and actual != size:
                bad.append(rel)
        return tuple(sorted(bad))
    except Exception:                                    # noqa: BLE001
        return ()


def write(model_dir: Path, relpaths: Iterable[str]) -> Path:
    """Record the sizes of `relpaths`. CALL ONLY WHEN THE DOWNLOAD IS COMPLETE.

    That sentence is the entire contract and it is not a style note. A manifest
    written early is worse than no manifest at all: it would certify a truncated
    file as the size it is supposed to be, and the check downstream would then
    vouch for exactly the folder it exists to catch. So this refuses to write
    anything if a named file is not there yet, rather than recording a partial
    size or quietly leaving the entry out.

    The sizes are taken from disk here rather than accepted from the caller, so
    a downloader cannot record a number that disagrees with the bytes it left
    behind.

    NO PRODUCTION CALLER TODAY, and that is a measured fact rather than an
    oversight: Elysium ships no model downloader (config.py:262-268, "the user
    drops a model directory into TTS_MODELS_DIR"), so there is no fetch whose
    completion this could hang off. It is here because the reader above is
    useless without one agreed way to produce the file, and a format with two
    implementations drifts into "no manifest" silently, which is the one
    failure this design cannot see. See the report on Q-28.

    Raises FileNotFoundError if a named file is absent, IsADirectoryError if a
    name is a folder, ValueError if a name leads outside the model folder or
    the same file is named twice, OSError if the folder cannot be written.
    Nothing is written on any of them.
    """
    model_dir = Path(model_dir)
    sizes: dict[str, int] = {}
    seen: set[str] = set()
    base = model_dir.resolve()
    for rel in relpaths:
        target = _inside(model_dir, base, rel)
        if target is None:
            raise ValueError(f"manifest entry leads outside the model: {rel!r}")
        # Our own writer must not be able to emit a file our own reader
        # refuses. `read` throws a manifest away whole when two keys are one
        # file under Windows casing, so producing one here would be a silent
        # way to ship a manifest that never checks anything.
        key = os.path.normcase(str(rel))
        if key in seen:
            raise ValueError(f"the same file is named twice: {rel!r}")
        seen.add(key)
        if target.is_dir():
            # Its own sentence. "is not there" about a name that is plainly
            # there sends the caller looking for the wrong thing.
            raise IsADirectoryError(
                f"a manifest records files, and {rel!r} is a folder"
            )
        if not target.is_file():
            raise FileNotFoundError(
                f"refusing to record an incomplete download: {rel!r} is not there"
            )
        sizes[str(rel)] = target.stat().st_size

    payload = {"version": MANIFEST_VERSION, "files": sizes}
    path = model_dir / MANIFEST_NAME
    # Temp file, fsync, replace - the same three steps and the same reason as
    # runtimes._save. os.replace repoints a directory entry and says nothing
    # about the bytes behind it, so without the fsync a power cut just after
    # the rename leaves a truncated manifest, and a truncated manifest reads as
    # "no manifest" - the check silently switches itself off on the one folder
    # that just finished downloading.
    fd, tmp = tempfile.mkstemp(dir=str(model_dir), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path
